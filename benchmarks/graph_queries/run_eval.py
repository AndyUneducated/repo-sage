"""Phase 1 graph-query benchmark runner.

Reads ``python_30.jsonl``, indexes the bundled ``tiny_python_repo`` fixture,
and for every question:

1. Picks a symbol from the question text via :class:`QueryRouter` (must
   match the symbol declared in the JSONL — sanity check on the parser
   regex).
2. Resolves the symbol to one or more nodes via
   :meth:`SQLiteSymbolGraphStore.find_nodes_by_suffix`.
3. Looks up callers via :meth:`SQLiteSymbolGraphStore.callers_of`.
4. Compares the *set* of caller FQNs to the truth set in the JSONL.

A question is correct iff the result set equals the expected set exactly.
Precision = correct / total. The roadmap exit metric is precision >= 0.90
on the 30-question fixture.

Usage::

    python -m benchmarks.graph_queries.run_eval               # uses tiny fixture
    python -m benchmarks.graph_queries.run_eval --large       # 50 kLOC perf
    python -m benchmarks.graph_queries.run_eval --threshold 0.95
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.router import QueryRouter
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "benchmarks" / "graph_queries" / "python_30.jsonl"
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tiny_python_repo"


@dataclass
class QuestionResult:
    qid: str
    question: str
    symbol: str
    detected: str | None
    expected: list[str]
    actual: list[str]

    @property
    def correct(self) -> bool:
        return sorted(self.actual) == sorted(self.expected)


def evaluate(
    questions_path: Path = DEFAULT_QUESTIONS,
    fixture_path: Path = DEFAULT_FIXTURE,
    repo_name: str = "tiny",
) -> tuple[list[QuestionResult], float]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo = tmp_path / "repo"
        shutil.copytree(fixture_path, repo)
        db = tmp_path / "index.db"
        IndexPipeline(repo=repo, sqlite_path=db, repo_name=repo_name).run(force=True)

        store = SQLiteSymbolGraphStore(db)
        store.init_schema()
        router = QueryRouter()

        results: list[QuestionResult] = []
        with questions_path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                detected = router.detect_symbol(rec["question"])
                callers: set[str] = set()
                if detected is not None:
                    for node in store.find_nodes_by_suffix(detected):
                        callers.update(c.src for c in store.callers_of(node.fqn))
                results.append(
                    QuestionResult(
                        qid=rec["id"],
                        question=rec["question"],
                        symbol=rec["symbol"],
                        detected=detected,
                        expected=list(rec["expected"]),
                        actual=sorted(callers),
                    )
                )
        store.close()
    correct = sum(1 for r in results if r.correct)
    precision = correct / max(len(results), 1)
    return results, precision


def benchmark_index_speed(repo: Path, repo_name: str = "perf") -> tuple[int, float]:
    """Return ``(n_python_files, elapsed_seconds)`` for indexing ``repo``."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.db"
        t0 = time.monotonic()
        manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name=repo_name).run(force=True)
        elapsed = time.monotonic() - t0
        return manifest.n_python_files, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 graph-query benchmark")
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
        help="Path to the question JSONL file.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Path to the python fixture to index.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.90,
        help="Minimum precision (0..1). Process exits non-zero on miss.",
    )
    parser.add_argument(
        "--large",
        action="store_true",
        help=(
            "Run the 50 kLOC indexing performance check instead of the precision "
            "benchmark. Reads $REPOSAGE_LARGE_REPO (default: ./.bench/large_repo)."
        ),
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=60.0,
        help="With --large, fail if indexing takes longer than this many seconds.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every question's verdict.",
    )
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
        n_files, elapsed = benchmark_index_speed(repo)
        print(f"indexed {n_files} python files in {elapsed:.2f}s")
        if elapsed > args.max_seconds:
            print(
                f"FAIL: index time {elapsed:.2f}s > target {args.max_seconds}s",
                file=sys.stderr,
            )
            return 1
        return 0

    results, precision = evaluate(args.questions, args.fixture)
    correct = sum(1 for r in results if r.correct)
    print(f"precision: {correct}/{len(results)} = {precision:.3f}")
    if args.verbose or precision < args.threshold:
        for r in results:
            mark = "OK " if r.correct else "FAIL"
            print(f"  [{mark}] {r.qid} sym={r.symbol!r} detected={r.detected!r}")
            if not r.correct:
                print(f"          expected={r.expected}")
                print(f"          actual=  {r.actual}")
    if precision < args.threshold:
        print(
            f"FAIL: precision {precision:.3f} < threshold {args.threshold:.3f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
