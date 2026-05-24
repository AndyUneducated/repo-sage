"""Phase 2 hybrid-RAG benchmark runner.

What it does:

1. Index the `tiny_python_repo` fixture into a temp SQLite DB with the
   `HashEmbedder` (deterministic; no model download in CI).
2. Build a `RetrievalService` from `LocalDenseIndex` + `BM25SparseRetriever`
   + `MockReranker` + `MockLLMClient`.
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
* `REPOSAGE_RAG_LLM=mock`: explicit fallback to `MockLLMClient`. CI and
  the eval-gate workflow set this so a forked PR with no Ollama box
  can still exercise the plumbing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from reposage.config import get_settings
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.llm.client import LiteLLMClient, MockLLMClient
from reposage.llm.grounding import extract_citations
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.protocols import LLMClient
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "benchmarks" / "rag" / "python_20.jsonl"
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tiny_python_repo"


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


def build_service(db: Path, repo_name: str, llm: LLMClient) -> RetrievalService:
    embedder = HashEmbedder()
    sparse = BM25SparseRetriever.from_sqlite(db, repo=repo_name)
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()
    return RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=llm,
    )


def index_repo(repo: Path, db: Path, repo_name: str) -> int:
    embedder = HashEmbedder()
    manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name=repo_name, embedder=embedder).run(
        force=True
    )
    return manifest.n_chunks


def load_questions(path: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    with path.open() as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


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
        index_repo(scratch, db, repo_name)
        service = build_service(db, repo_name, llm)

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


class OllamaUnavailableError(RuntimeError):
    """Ollama health check failed; surfaces a clear remediation hint."""


_OLLAMA_MODEL_PREFIX = ("ollama/", "ollama_chat/")


def _strip_ollama_prefix(model: str) -> str:
    for p in _OLLAMA_MODEL_PREFIX:
        if model.startswith(p):
            return model[len(p) :]
    return model


def _check_ollama(api_base: str, model: str, timeout: float = 2.0) -> None:
    """Verify Ollama is reachable AND has the requested model pulled.

    A bare /api/tags ping confirms the daemon is up. We then look for the
    model in the tags list so an unpulled tag fails fast with a clear
    `ollama pull <name>` hint instead of letting LiteLLM surface an opaque
    APIConnectionError mid-bench.
    """
    bare = _strip_ollama_prefix(model)
    url = api_base.rstrip("/") + "/api/tags"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                raise OllamaUnavailableError(f"GET {url} -> HTTP {resp.status}")
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaUnavailableError(
            f"Cannot reach Ollama at {api_base}: {exc}.\n"
            f"Either start Ollama (`ollama serve` and `ollama pull "
            f"{bare}`) or set REPOSAGE_RAG_LLM=mock to fall back to the "
            "offline mock pipeline."
        ) from exc

    available = {str(m.get("name", "")) for m in payload.get("models", [])}
    # Ollama tags include the explicit `:tag` suffix. Treat `name` and
    # `name:latest` as equivalent so `llama3` matches `llama3:latest`.
    candidates = {bare} if ":" in bare else {bare, f"{bare}:latest"}
    if not (candidates & available):
        pretty = ", ".join(sorted(available)) or "(none)"
        raise OllamaUnavailableError(
            f"Ollama is reachable at {api_base} but model {bare!r} is not "
            f"pulled. Available: {pretty}.\n"
            f"Run `ollama pull {bare}` or set REPOSAGE_RAG_LLM=mock to use "
            "the offline mock pipeline."
        )


def make_llm() -> LLMClient:
    """Return the LLM the bench should use.

    DD-014: defaults to a real LiteLLM client (Ollama is the configured
    default provider). The mock branch is reachable only with an
    explicit env var so a missing Ollama daemon never silently downgrades
    benchmark numbers.
    """
    flag = os.environ.get("REPOSAGE_RAG_LLM", "").lower()
    if flag == "mock":
        return MockLLMClient()
    settings = get_settings()
    model = settings.llm_model
    if model.startswith(_OLLAMA_MODEL_PREFIX):
        _check_ollama(settings.ollama_api_base, model)
    return LiteLLMClient()


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
            "REPOSAGE_RAG_LLM=mock and 60000ms with a real LLM."
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
            "REPOSAGE_RAG_LLM=mock and 0.90 with a real LLM (DD-013)."
        ),
    )
    parser.add_argument(
        "--large",
        action="store_true",
        help="Run against $REPOSAGE_LARGE_REPO instead of the tiny fixture.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.large:
        repo_str = os.environ.get("REPOSAGE_LARGE_REPO", str(REPO_ROOT / ".bench" / "large_repo"))
        repo = Path(repo_str)
        if not repo.exists():
            print(
                f"large repo not found at {repo}; skipping perf check.\n"
                "Set REPOSAGE_LARGE_REPO to a >=50 kLOC python checkout to enable.",
                file=sys.stderr,
            )
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
    results = asyncio.run(
        run_eval(
            questions=questions,
            repo=repo,
            repo_name=repo_name,
            llm=llm,
            top_k=args.top_k,
        )
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
