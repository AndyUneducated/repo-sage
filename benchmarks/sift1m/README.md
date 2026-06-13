# SIFT-1M ANN Benchmark

Compares `go-hnsw` against Faiss (HNSWFlat) on the standard SIFT-1M dataset.

## Methodology

* **Dataset**: SIFT-1M (1M base, 10k query, 100 ground-truth neighbours per query, 128-d L2).
* **Sweep**: `M ∈ {8, 16, 32}` × `efConstruction ∈ {100, 200, 400}` × `efSearch ∈ {16, 32, 64, 128, 256}`.
* **Metrics**: build wall-time, peak RSS, recall@10, single-thread QPS, P50 / P99 latency.
* **Hardware**: documented in each result CSV (`uname -a`, `sysctl -n machdep.cpu.brand_string`, free memory).

## Goals (Phase 4 deliverable)

* Plot recall-vs-QPS Pareto frontier; document where `go-hnsw` lands relative to Faiss.
* Be honest: if `go-hnsw` is *N*× slower than Faiss at the same recall, *report N*. The valuable artefact is the Pareto curve and the explanation, not a meaningless win.
* Validate the Phase 4 exit metric: reloading the 1M × 128 mmap snapshot has P50 `< 200 ms` (the `recover_p50_ms` column).

## Files

* `run_sweep.py` — fans the grid out to the Go `hnsw-bench`, overlays the Faiss baseline, computes the Pareto frontier, plots it, and refreshes the table in `docs/BENCHMARKS.md`.
* `faiss_baseline.py` — `IndexHNSWFlat` baseline emitting the same CSV columns; single-threaded to match.
* `fetch_sift1m.sh` — downloads + unpacks SIFT-1M into `data/` (not committed).

## Reproducing

```bash
# 0) build the Go bench binary
make hnsw-build

# 1) CI smoke (no download): synthetic build -> snapshot -> recover -> query
make bench-sift-synthetic

# 2) full SIFT-1M run vs Faiss, with Pareto plot + doc refresh
bash benchmarks/sift1m/fetch_sift1m.sh        # ~1 GB into data/sift/
pip install -e ".[bench]"                     # faiss-cpu + matplotlib
python benchmarks/sift1m/run_sweep.py \
  --dataset-dir benchmarks/sift1m/data/sift \
  --snapshot benchmarks/sift1m/data/index.hnsw --faiss --write-docs
```

CSV rows land in `results/<date>-sift-sweep.csv`; the Pareto plot in `results/<date>-sift-pareto.png`. Both directories are git-ignored.
