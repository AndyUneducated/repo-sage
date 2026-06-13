"""Unit tests for the SIFT-1M sweep driver's pure helpers.

These cover CSV parsing, Pareto-frontier selection, the markdown table render,
and the in-place BENCHMARKS.md marker replacement — none of which need Go,
faiss, matplotlib, or the dataset.
"""

from __future__ import annotations

from benchmarks.sift1m import run_sweep


def _row(index: str, recall: float, qps: float, **extra: float) -> dict[str, object]:
    base = {
        "index": index,
        "M": 16,
        "efC": 200,
        "efSearch": 64,
        "recall": recall,
        "qps": qps,
        "p50_ms": 0.1,
        "p99_ms": 0.2,
        "build_s": 1.0,
        "rss_mb": 10.0,
        "recover_p50_ms": 5.0,
        "n": 1000,
        "dim": 128,
    }
    base.update(extra)
    return base


def test_parse_csv_text_types() -> None:
    text = "\n".join(
        [
            ",".join(run_sweep.COLUMNS),
            "go-hnsw,16,200,64,0.9000,4500.5,0.21,0.34,5.39,27.5,0.10,8000,128",
        ]
    )
    rows = run_sweep.parse_csv_text(text)
    assert len(rows) == 1
    r = rows[0]
    assert r["index"] == "go-hnsw"
    assert r["M"] == 16 and isinstance(r["M"], int)
    assert r["recall"] == 0.9 and isinstance(r["recall"], float)
    assert r["n"] == 8000


def test_pareto_front_drops_dominated() -> None:
    rows = [
        _row("go-hnsw", recall=0.80, qps=5000),  # dominated by the 0.85/6000 point
        _row("go-hnsw", recall=0.85, qps=6000),  # frontier
        _row("go-hnsw", recall=0.95, qps=2000),  # frontier (higher recall)
        _row("go-hnsw", recall=0.70, qps=3000),  # dominated
    ]
    front = run_sweep.pareto_front(rows)
    recalls = sorted(float(r["recall"]) for r in front)
    assert recalls == [0.85, 0.95]


def test_pareto_front_keeps_all_when_tradeoff() -> None:
    rows = [
        _row("go-hnsw", recall=0.6, qps=9000),
        _row("go-hnsw", recall=0.8, qps=5000),
        _row("go-hnsw", recall=0.95, qps=2000),
    ]
    front = run_sweep.pareto_front(rows)
    assert len(front) == 3


def test_markdown_table_has_header_and_rows() -> None:
    table = run_sweep.markdown_table([_row("go-hnsw", 0.9, 4500), _row("faiss", 0.92, 9000)])
    assert "Recall@10" in table
    assert "go-hnsw" in table and "faiss" in table
    # header + separator + 2 data rows
    assert len(table.splitlines()) == 4


def test_write_benchmarks_md_replaces_between_markers(tmp_path, monkeypatch) -> None:
    doc = tmp_path / "BENCHMARKS.md"
    doc.write_text(
        f"before\n{run_sweep.SIFT_TABLE_START}\nOLD TABLE\n{run_sweep.SIFT_TABLE_END}\nafter\n"
    )
    monkeypatch.setattr(run_sweep, "BENCHMARKS_MD", doc)

    run_sweep.write_benchmarks_md("NEW TABLE", "results/plot.png")
    out = doc.read_text()
    assert "OLD TABLE" not in out
    assert "NEW TABLE" in out
    assert "results/plot.png" in out
    assert out.startswith("before\n")
    assert out.rstrip().endswith("after")
