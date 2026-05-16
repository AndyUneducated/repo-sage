# SIFT-1M ANN Benchmark

Compares `go-hnsw` against Faiss (HNSWFlat) on the standard SIFT-1M dataset.

## Methodology

* **Dataset**: SIFT-1M (1M base, 10k query, 100 ground-truth neighbours per query, 128-d L2).
* **Sweep**: `M ∈ {8, 16, 32}` × `efConstruction ∈ {100, 200, 400}` × `efSearch ∈ {16, 32, 64, 128, 256}`.
* **Metrics**: build wall-time, peak RSS, recall@10, single-thread QPS, P50 / P99 latency.
* **Hardware**: documented in each result CSV (`uname -a`, `sysctl -n machdep.cpu.brand_string`, free memory).

## Goals (Phase 5 deliverable)

* Plot recall-vs-QPS Pareto frontier; document where `go-hnsw` lands relative to Faiss.
* Be honest: if `go-hnsw` is *N*× slower than Faiss at the same recall, *report N*. The valuable artefact is the Pareto curve and the explanation, not a meaningless win.

## Reproducing

```bash
make hnsw-bench
# or, for the full sweep with plots:
python benchmarks/sift1m/run_sweep.py
```
