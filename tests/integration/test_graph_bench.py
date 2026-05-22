"""Phase 1 exit-metric gate as a pytest test.

Runs the same benchmark as ``make bench-graph`` and asserts precision
>= 0.90 (the roadmap exit threshold). Kept in the standard test suite so
``pytest -q`` is the single command CI runs.
"""

from __future__ import annotations

from benchmarks.graph_queries.run_eval import evaluate

PRECISION_THRESHOLD = 0.90


def test_python_30_question_precision_at_threshold() -> None:
    results, precision = evaluate()
    assert len(results) == 30
    misses = [r for r in results if not r.correct]
    diagnostic = "\n".join(
        f"  {r.qid} sym={r.symbol!r} expected={r.expected} actual={r.actual}" for r in misses
    )
    assert precision >= PRECISION_THRESHOLD, (
        f"precision {precision:.3f} < {PRECISION_THRESHOLD:.3f}\n"
        f"{len(misses)} miss(es):\n{diagnostic}"
    )
