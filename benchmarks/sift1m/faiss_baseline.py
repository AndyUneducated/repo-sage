"""Faiss `IndexHNSWFlat` baseline for the SIFT-1M sweep.

Emits CSV rows in the same column order as the Go `hnsw-bench` so
`run_sweep.py` can overlay the two on one Pareto plot. Single-threaded to match
the Go harness. faiss is an optional dependency (`pip install '.[bench]'`); when
it is missing this script prints a hint to stderr and exits 0 with no rows so
the sweep degrades to go-hnsw only.

Meaningful go-vs-faiss comparison requires `--dataset-dir` (both read the same
SIFT files). `--synthetic` here is an independent smoke set, not comparable to
the Go synthetic data.
"""

from __future__ import annotations

import argparse
import resource
import sys
import time
from pathlib import Path

import numpy as np


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def rss_mb() -> float:
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KB, macOS reports bytes.
    return maxrss / 1024.0 if sys.platform != "darwin" else maxrss / (1024.0 * 1024.0)


def read_fvecs(path: Path, limit: int = 0) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.int32)
    if raw.size == 0:
        return np.zeros((0, 0), dtype=np.float32)
    dim = int(raw[0])
    rowlen = dim + 1
    rows = raw.reshape(-1, rowlen)
    vecs = rows[:, 1:].view(np.float32)
    if limit > 0:
        vecs = vecs[:limit]
    return np.ascontiguousarray(vecs, dtype=np.float32)


def read_ivecs(path: Path, limit: int = 0) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.int32)
    if raw.size == 0:
        return np.zeros((0, 0), dtype=np.int32)
    dim = int(raw[0])
    rows = raw.reshape(-1, dim + 1)[:, 1:]
    if limit > 0:
        rows = rows[:limit]
    return np.ascontiguousarray(rows, dtype=np.int32)


def brute_force_gt(base: np.ndarray, queries: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros((queries.shape[0], k), dtype=np.int32)
    for i, q in enumerate(queries):
        d = np.sum((base - q) ** 2, axis=1)
        out[i] = np.argsort(d, kind="stable")[:k]
    return out


def load_dataset(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if args.dataset_dir:
        d = Path(args.dataset_dir)
        base = read_fvecs(d / "sift_base.fvecs", args.max_base)
        queries = read_fvecs(d / "sift_query.fvecs", args.max_queries)
        if args.max_base:
            gt = brute_force_gt(base, queries, args.topk)
        else:
            gt = read_ivecs(d / "sift_groundtruth.ivecs", args.max_queries)
        return base, queries, gt
    rng = np.random.default_rng(1337)
    base = rng.standard_normal((args.synthetic, 128)).astype(np.float32)
    queries = rng.standard_normal((args.synthetic_queries, 128)).astype(np.float32)
    return base, queries, brute_force_gt(base, queries, args.topk)


def recall_at_k(got: np.ndarray, truth: np.ndarray, k: int) -> float:
    total = 0.0
    for g, t in zip(got, truth, strict=False):
        tset = set(t[:k].tolist())
        hit = sum(1 for x in g[:k].tolist() if x in tset)
        total += hit / k
    return total / len(got)


def percentile(latencies_ms: list[float], p: float) -> float:
    if not latencies_ms:
        return 0.0
    return float(np.percentile(np.array(latencies_ms), p))


def main() -> int:
    parser = argparse.ArgumentParser(description="Faiss HNSW baseline for SIFT-1M")
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--synthetic", type=int, default=20000)
    parser.add_argument("--synthetic-queries", type=int, default=100)
    parser.add_argument("--M", nargs="+", type=int, default=[16])
    parser.add_argument("--efC", nargs="+", type=int, default=[200])
    parser.add_argument("--ef", nargs="+", type=int, default=[16, 32, 64, 128, 256])
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--max-base", type=int, default=0)
    parser.add_argument("--max-queries", type=int, default=0)
    args = parser.parse_args()

    try:
        import faiss  # noqa: PLC0415
    except ImportError:
        log("faiss not installed; skipping baseline (pip install '.[bench]')")
        return 0

    faiss.omp_set_num_threads(1)
    base, queries, gt = load_dataset(args)
    if base.shape[0] == 0 or queries.shape[0] == 0:
        log("faiss baseline: empty dataset")
        return 0
    dim = base.shape[1]
    log(f"faiss: base={base.shape[0]} queries={queries.shape[0]} dim={dim}")

    for m in args.M:
        for efc in args.efC:
            index = faiss.IndexHNSWFlat(dim, m)
            index.hnsw.efConstruction = efc
            t0 = time.perf_counter()
            index.add(base)
            build_s = time.perf_counter() - t0
            rss = rss_mb()
            for ef in args.ef:
                index.hnsw.efSearch = ef
                latencies: list[float] = []
                got = np.zeros((queries.shape[0], args.topk), dtype=np.int64)
                index.search(queries[:1], args.topk)  # warmup
                for i, q in enumerate(queries):
                    s = time.perf_counter()
                    _, idx = index.search(q.reshape(1, -1), args.topk)
                    latencies.append((time.perf_counter() - s) * 1000.0)
                    got[i] = idx[0]
                total_s = sum(latencies) / 1000.0
                qps = len(queries) / total_s if total_s > 0 else 0.0
                recall = recall_at_k(got, gt, args.topk)
                print(
                    f"faiss,{m},{efc},{ef},{recall:.4f},{qps:.1f},"
                    f"{percentile(latencies, 50):.4f},{percentile(latencies, 99):.4f},"
                    f"{build_s:.2f},{rss:.1f},0.000,{base.shape[0]},{dim}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
