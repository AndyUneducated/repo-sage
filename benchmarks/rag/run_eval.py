"""Phase 2 hybrid-RAG benchmark runner.

What it does:

1. Index the `tiny_python_repo` fixture into a temp SQLite DB with the
   `HashEmbedder` (deterministic; no model download in CI).
2. Build a `RetrievalService` from `LocalDenseIndex` + `BM25SparseRetriever`
   + `MockReranker` + the configured LLM.
3. For each question in `python_20.jsonl`, run `service.answer(...)`,
   record latency and citation-legality, and compute file-level recall@5
   over the top retrieved chunks.

Pass criteria (Phase 2 ROADMAP):
* P50 latency < 1.5 s on the tiny fixture.
* Citation legality == 1.0 (the grounder must never let a fabricated
  citation through).
* file-level recall@5 >= 0.80 across the 20-question set.

`--large` runs the same 20 questions against a >=50 kLOC repo pointed at
by `REPOSAGE_LARGE_REPO`. The recall threshold is informational there;
the latency target is the primary signal.

LLM selection (DD-014):

* Default: real LLM via LiteLLM (Ollama is the default provider; set
  `OLLAMA_API_BASE` to point at a non-default daemon). The runner pings
  the configured Ollama endpoint before indexing and fails fast if it
  cannot reach it, so a misconfigured local box never silently degrades
  recall/latency numbers.
* `REPOSAGE_PROFILE=mock`: explicit fallback to `MockLLMClient`. CI and
  the eval-gate workflow set this so a forked PR with no Ollama box
  can still exercise the plumbing.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from reposage.composition import build_llm, current_profile
from reposage.config import get_settings
from reposage.llm.client import MockLLMClient
from reposage.llm.grounding import extract_citations
from reposage.retrieval.protocols import LLMClient

from benchmarks._common import (
    DEFAULT_FIXTURE,
    OllamaUnavailableError,
    add_large_arg,
    add_verbose_arg,
    check_ollama_available,
    index_fixture_and_build_service,
    is_ollama_model,
    load_questions,
    resolve_large_repo,
    run_with_aclose,
)

DEFAULT_QUESTIONS = Path(__file__).parent / "python_20.jsonl"


@dataclass
class QuestionResult:
    qid: str
    question: str
    expected_paths: list[str]
    answer: str
    citations: list[str]
    chunk_paths: list[str]
    latency_ms: int
    grounded: bool

    @property
    def recall_at_k(self) -> float:
        if not self.expected_paths:
            return 1.0
        hit = sum(1 for p in self.expected_paths if p in self.chunk_paths)
        return hit / len(self.expected_paths)

    @property
    def citation_legal(self) -> bool:
        return self.grounded


async def run_eval(
    *,
    questions: list[dict[str, object]],
    repo: Path,
    repo_name: str,
    llm: LLMClient,
    top_k: int,
) -> list[QuestionResult]:
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "repo"
        shutil.copytree(repo, scratch)
        db = Path(tmp) / "index.db"
        service = index_fixture_and_build_service(
            fixture=scratch,
            db=db,
            repo_name=repo_name,
            llm=llm,
            graphrag=False,
        )

        results: list[QuestionResult] = []
        for q in questions:
            t0 = time.monotonic()
            res = await service.answer(
                str(q["question"]),
                repo=repo_name,
                route_hint="hybrid",
                top_k=top_k,
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            chunk_paths = [str(c.path) for c in res.chunks]
            cites = [f"{c.path}:{c.start_line}-{c.end_line}" for c in extract_citations(res.answer)]
            results.append(
                QuestionResult(
                    qid=str(q["id"]),
                    question=str(q["question"]),
                    expected_paths=list(q.get("expected_paths") or []),  # type: ignore[arg-type]
                    answer=res.answer,
                    citations=cites,
                    chunk_paths=chunk_paths,
                    latency_ms=elapsed_ms,
                    grounded=res.grounded,
                )
            )
    return results


def summarise(results: list[QuestionResult]) -> dict[str, float]:
    latencies = sorted(r.latency_ms for r in results)
    p50 = statistics.median(latencies)
    p95_idx = max(round(0.95 * len(latencies)) - 1, 0)
    p95 = latencies[p95_idx]
    citation_legal = sum(1 for r in results if r.citation_legal) / max(len(results), 1)
    recall = sum(r.recall_at_k for r in results) / max(len(results), 1)
    return {
        "p50_ms": float(p50),
        "p95_ms": float(p95),
        "citation_legal_rate": citation_legal,
        "recall_at_k": recall,
        "n": float(len(results)),
    }


def make_llm() -> LLMClient:
    """Return the LLM the bench should use.

    DD-014: defaults to a real LiteLLM client (Ollama is the configured
    default provider). `REPOSAGE_PROFILE=mock` switches to `MockLLMClient`
    so a missing Ollama daemon never silently downgrades benchmark
    numbers. Health-checks the daemon up front when targeting Ollama.
    """
    if current_profile() == "mock":
        return build_llm()
    settings = get_settings()
    if is_ollama_model(settings.llm_model):
        check_ollama_available(settings.llm_model, settings.ollama_api_base)
    return build_llm()


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 hybrid RAG benchmark")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--p50-budget-ms",
        type=float,
        default=None,
        help=(
            "Maximum acceptable median latency. Defaults to 1500ms with "
            "REPOSAGE_PROFILE=mock and 60000ms with a real LLM."
        ),
    )
    parser.add_argument(
        "--recall-threshold",
        type=float,
        default=0.80,
        help="Minimum file-level recall@k (LLM-independent).",
    )
    parser.add_argument(
        "--citation-threshold",
        type=float,
        default=None,
        help=(
            "Minimum fraction of grounded answers. Defaults to 1.0 with "
            "REPOSAGE_PROFILE=mock and 0.90 with a real LLM (DD-013)."
        ),
    )
    add_large_arg(parser)
    add_verbose_arg(parser)
    args = parser.parse_args()

    if args.large:
        repo = resolve_large_repo()
        if repo is None:
            return 0
        repo_name = "perf"
    else:
        repo = args.fixture
        repo_name = "tiny"

    questions = load_questions(args.questions)
    try:
        llm = make_llm()
    except OllamaUnavailableError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    is_mock = isinstance(llm, MockLLMClient)
    p50_budget = (
        args.p50_budget_ms if args.p50_budget_ms is not None else (1500.0 if is_mock else 60_000.0)
    )
    citation_threshold = (
        args.citation_threshold
        if args.citation_threshold is not None
        else (1.0 if is_mock else 0.90)
    )

    results = run_with_aclose(
        lambda: run_eval(
            questions=questions,
            repo=repo,
            repo_name=repo_name,
            llm=llm,
            top_k=args.top_k,
        ),
        llm,
    )
    summary = summarise(results)

    mode = "mock" if is_mock else f"real ({llm.model})"
    print(
        f"[{mode}] n={int(summary['n'])} "
        f"P50={summary['p50_ms']:.0f}ms "
        f"P95={summary['p95_ms']:.0f}ms "
        f"recall@{args.top_k}={summary['recall_at_k']:.3f} "
        f"citation_legal={summary['citation_legal_rate']:.3f}"
    )

    if args.verbose:
        for r in results:
            print(
                f"  {r.qid} {r.latency_ms:>5}ms  "
                f"recall={r.recall_at_k:.2f}  legal={r.citation_legal}  "
                f"q={r.question}"
            )
            for cite in r.citations:
                print(f"      cite={cite}")

    fail = False
    if summary["p50_ms"] > p50_budget:
        print(
            f"FAIL: P50 {summary['p50_ms']:.0f}ms > budget {p50_budget:.0f}ms",
            file=sys.stderr,
        )
        fail = True
    if summary["recall_at_k"] < args.recall_threshold:
        print(
            f"FAIL: recall {summary['recall_at_k']:.3f} < threshold {args.recall_threshold:.3f}",
            file=sys.stderr,
        )
        fail = True
    if summary["citation_legal_rate"] < citation_threshold:
        print(
            f"FAIL: citation legality {summary['citation_legal_rate']:.3f} "
            f"< threshold {citation_threshold:.3f}",
            file=sys.stderr,
        )
        fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
