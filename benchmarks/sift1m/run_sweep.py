"""SIFT-1M sweep driver for the Phase 4 ANN benchmark.

Runs the Go `hnsw-bench` binary across a parameter grid, optionally appends a
Faiss `IndexHNSWFlat` baseline on the same data, computes the recall-vs-QPS
Pareto frontier, plots it, and refreshes the table in `docs/BENCHMARKS.md`.

The heavy lifting (build / search / recall / recover-P50) lives in Go; this
driver only fans out configurations, parses the CSV rows, and renders the
deliverable. With no `--dataset-dir` it falls back to a synthetic set so the
whole pipeline is exercisable without the 1 GB SIFT download.

    python benchmarks/sift1m/run_sweep.py --dataset-dir benchmarks/sift1m/data --faiss
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GO_BENCH = REPO_ROOT / "go-hnsw" / "bin" / "hnsw-bench"
HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
BENCHMARKS_MD = REPO_ROOT / "docs" / "BENCHMARKS.md"

SIFT_TABLE_START = "<!-- SIFT_TABLE_START -->"
SIFT_TABLE_END = "<!-- SIFT_TABLE_END -->"

COLUMNS = [
    "index",
    "M",
    "efC",
    "efSearch",
    "recall",
    "qps",
    "p50_ms",
    "p99_ms",
    "build_s",
    "rss_mb",
    "recover_p50_ms",
    "n",
    "dim",
]
INT_COLS = {"M", "efC", "efSearch", "n", "dim"}


def parse_csv_text(text: str) -> list[dict[str, object]]:
    """Parse hnsw-bench / faiss CSV output into typed row dicts."""
    rows: list[dict[str, object]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        if not raw.get("index"):
            continue
        row: dict[str, object] = {}
        for col in COLUMNS:
            val = raw.get(col, "")
            if col == "index":
                row[col] = val
            elif col in INT_COLS:
                row[col] = int(float(val)) if val else 0
            else:
                row[col] = float(val) if val else 0.0
        rows.append(row)
    return rows


def pareto_front(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return rows on the recall-vs-QPS Pareto frontier (maximise both).

    A row is dominated when another row has recall >= and QPS >= with at least
    one strictly greater; dominated rows are dropped. The frontier is returned
    sorted by recall ascending.
    """
    front: list[dict[str, object]] = []
    for i, a in enumerate(rows):
        ar, aq = float(a["recall"]), float(a["qps"])
        dominated = False
        for j, b in enumerate(rows):
            if i == j:
                continue
            br, bq = float(b["recall"]), float(b["qps"])
            if br >= ar and bq >= aq and (br > ar or bq > aq):
                dominated = True
                break
        if not dominated:
            front.append(a)
    front.sort(key=lambda r: float(r["recall"]))
    return front


def run_go_bench(args: argparse.Namespace) -> str:
    """Invoke the Go bench binary once over the whole grid and return its CSV."""
    if not GO_BENCH.exists():
        raise SystemExit(f"go bench binary missing: {GO_BENCH} (run `make hnsw-build` first)")
    cmd = [
        str(GO_BENCH),
        "--M",
        ",".join(map(str, args.M)),
        "--efC",
        ",".join(map(str, args.efC)),
        "--ef",
        ",".join(map(str, args.ef)),
        "--topk",
        str(args.topk),
        "--recover-runs",
        str(args.recover_runs),
        "--header",
    ]
    if args.dataset_dir:
        cmd += ["--dataset-dir", args.dataset_dir, "--metric", "l2"]
        if args.max_base:
            cmd += ["--max-base", str(args.max_base)]
        if args.max_queries:
            cmd += ["--max-queries", str(args.max_queries)]
    else:
        cmd += ["--synthetic", str(args.synthetic)]
    if args.snapshot:
        cmd += ["--snapshot", args.snapshot]
    print("$", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    return proc.stdout


def run_faiss_baseline(args: argparse.Namespace) -> str:
    """Run the Faiss baseline script and return its CSV (no header)."""
    script = HERE / "faiss_baseline.py"
    # faiss_baseline.py uses argparse `nargs="+"`, so each grid axis must be
    # passed as separate space-separated tokens — NOT comma-joined (the Go
    # bench accepts commas, faiss does not).
    cmd = [
        sys.executable,
        str(script),
        "--M",
        *map(str, args.M),
        "--efC",
        *map(str, args.efC),
        "--ef",
        *map(str, args.ef),
        "--topk",
        str(args.topk),
    ]
    if args.dataset_dir:
        cmd += ["--dataset-dir", args.dataset_dir]
        if args.max_base:
            cmd += ["--max-base", str(args.max_base)]
        if args.max_queries:
            cmd += ["--max-queries", str(args.max_queries)]
    else:
        cmd += ["--synthetic", str(args.synthetic)]
    print("$", " ".join(cmd), file=sys.stderr)
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    return proc.stdout


def markdown_table(rows: list[dict[str, object]]) -> str:
    """Render rows as the BENCHMARKS.md SIFT table."""
    header = (
        "| Index | M | efC | efSearch | Recall@10 | QPS (1 thread) | "
        "P50 (ms) | P99 (ms) | Build (s) | RSS (MB) | Recover P50 (ms) |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"| {r['index']} | {r['M']} | {r['efC']} | {r['efSearch']} "
            f"| {float(r['recall']):.4f} | {float(r['qps']):.0f} "
            f"| {float(r['p50_ms']):.3f} | {float(r['p99_ms']):.3f} "
            f"| {float(r['build_s']):.1f} | {float(r['rss_mb']):.0f} "
            f"| {float(r['recover_p50_ms']):.2f} |"
        )
    return "\n".join(lines)


def plot_pareto(rows: list[dict[str, object]], out_png: Path) -> bool:
    """Scatter every config and outline each index's Pareto frontier.

    Returns False (without raising) when matplotlib is not installed, so the
    sweep still produces the CSV + table in a minimal environment.
    """
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        print(
            "matplotlib not installed; skipping Pareto plot (pip install '.[bench]')",
            file=sys.stderr,
        )
        return False

    fig, ax = plt.subplots(figsize=(7, 5))
    indices = sorted({str(r["index"]) for r in rows})
    for idx in indices:
        pts = [r for r in rows if str(r["index"]) == idx]
        ax.scatter(
            [float(r["recall"]) for r in pts],
            [float(r["qps"]) for r in pts],
            s=18,
            alpha=0.5,
            label=f"{idx} (all configs)",
        )
        front = pareto_front(pts)
        ax.plot(
            [float(r["recall"]) for r in front],
            [float(r["qps"]) for r in front],
            marker="o",
            linewidth=2,
            label=f"{idx} (Pareto)",
        )
    ax.set_xlabel("Recall@10")
    ax.set_ylabel("QPS (single thread)")
    ax.set_yscale("log")
    ax.set_title("SIFT-1M: recall vs QPS Pareto frontier")
    ax.grid(True, which="both", linestyle=":", alpha=0.4)
    ax.legend(fontsize=8)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return True


def write_benchmarks_md(table: str, png_rel: str | None) -> None:
    """Replace the marked SIFT block in docs/BENCHMARKS.md in place."""
    if not BENCHMARKS_MD.exists():
        print(f"{BENCHMARKS_MD} missing; skipping doc update", file=sys.stderr)
        return
    text = BENCHMARKS_MD.read_text()
    if SIFT_TABLE_START not in text or SIFT_TABLE_END not in text:
        print("BENCHMARKS.md has no SIFT markers; skipping doc update", file=sys.stderr)
        return
    block = [SIFT_TABLE_START, "", table, ""]
    if png_rel:
        block += [f"![SIFT-1M recall vs QPS Pareto frontier]({png_rel})", ""]
    block.append(SIFT_TABLE_END)
    pre = text.split(SIFT_TABLE_START)[0]
    post = text.split(SIFT_TABLE_END)[1]
    BENCHMARKS_MD.write_text(pre + "\n".join(block) + post)
    print(f"updated {BENCHMARKS_MD}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="SIFT-1M recall/QPS sweep + Pareto plot")
    parser.add_argument("--dataset-dir", default="", help="SIFT data dir (empty -> synthetic)")
    parser.add_argument("--synthetic", type=int, default=20000, help="synthetic base size")
    parser.add_argument("--M", nargs="+", type=int, default=[8, 16, 32])
    parser.add_argument("--efC", nargs="+", type=int, default=[100, 200, 400])
    parser.add_argument("--ef", nargs="+", type=int, default=[16, 32, 64, 128, 256])
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--recover-runs", type=int, default=5)
    parser.add_argument("--max-base", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--snapshot", default="", help="snapshot path to measure reload P50")
    parser.add_argument("--faiss", action="store_true", help="also run the Faiss baseline")
    parser.add_argument("--write-docs", action="store_true", help="refresh docs/BENCHMARKS.md")
    parser.add_argument(
        "--go-csv",
        default="",
        help="read go-hnsw rows from this CSV instead of running the bench (offline replot)",
    )
    parser.add_argument(
        "--faiss-csv",
        default="",
        help="read Faiss rows from this CSV instead of running the baseline",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    csv_path = RESULTS_DIR / f"{stamp}-sift-sweep.csv"

    def persist(rows: list[dict[str, object]]) -> None:
        """Write the combined CSV. Called after the (expensive) go run and again
        after the Faiss baseline so a later failure can never discard results."""
        with csv_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)  # type: ignore[arg-type]
        print(f"wrote {csv_path} ({len(rows)} rows)", file=sys.stderr)

    if args.go_csv:
        rows = parse_csv_text(Path(args.go_csv).read_text())
    else:
        rows = parse_csv_text(run_go_bench(args))
    # Persist go-hnsw rows immediately — the build is the multi-hour part, so
    # never let a downstream Faiss/plot error throw it away.
    persist(rows)

    if args.faiss or args.faiss_csv:
        faiss_out = Path(args.faiss_csv).read_text() if args.faiss_csv else run_faiss_baseline(args)
        # faiss_baseline emits headerless rows; prepend the shared columns
        # unless the source already carries a header.
        if not faiss_out.lstrip().startswith("index,"):
            faiss_out = "\n".join([",".join(COLUMNS), faiss_out])
        rows += parse_csv_text(faiss_out)
        persist(rows)

    png_path = RESULTS_DIR / f"{stamp}-sift-pareto.png"
    png_rel = None
    if plot_pareto(rows, png_path):
        png_rel = f"../../benchmarks/sift1m/results/{png_path.name}"
        print(f"wrote {png_path}", file=sys.stderr)

    # The published table shows each index's OWN recall-vs-QPS frontier, not the
    # global one: when one library dominates (e.g. Faiss > go-hnsw on QPS) the
    # global frontier would hide the loser entirely, defeating the comparison.
    table_rows: list[dict[str, object]] = []
    for idx in sorted({str(r["index"]) for r in rows}):
        table_rows += pareto_front([r for r in rows if str(r["index"]) == idx])

    print("\n=== per-index Pareto frontier (recall, qps) ===", file=sys.stderr)
    for r in table_rows:
        print(
            f"  {r['index']:>8}  M={r['M']:<3} efC={r['efC']:<4} ef={r['efSearch']:<4} "
            f"recall={float(r['recall']):.4f}  qps={float(r['qps']):.0f}",
            file=sys.stderr,
        )

    if args.write_docs:
        write_benchmarks_md(markdown_table(table_rows), png_rel)


if __name__ == "__main__":
    main()
