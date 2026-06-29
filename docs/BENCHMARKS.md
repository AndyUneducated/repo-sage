# Benchmarks

This file is the single source of truth for any number we publish about RepoSage. Numbers land here as they are produced; the README links here rather than embedding stale figures.

## 1. SIFT-1M ANN benchmark — go-hnsw vs Faiss

* Dataset: SIFT-1M (1M × 128-d, 10k queries, ground-truth top-100, L2 metric) — the full base set, not a subset.
* Configuration sweep (first published run, reduced grid): `M ∈ {16, 32}` × `efC ∈ {200, 400}` × `efSearch ∈ {16, 32, 64, 128, 256}` — 20 configs per index. The wider `M ∈ {8, 16, 32}` × `efC ∈ {100, 200, 400}` grid is the eventual target; it is left to a follow-up run because each go-hnsw 1M build takes ~22–68 min single-threaded.
* Single thread for both indexes (`faiss.omp_set_num_threads(1)`); one query per `Search` call so the P50 / P99 reflect real per-query latency, not batch throughput.
* Recover P50 is the median of 5 reloads of the mmap snapshot — the Phase 4 exit metric (`< 200 ms` for 1M × 128).
* Hardware: Apple M4 (arm64), macOS; numbers reproduced per row in `benchmarks/sift1m/results/<date>-sift-sweep.csv`.

Reproduce:

```bash
make hnsw-build
bash benchmarks/sift1m/fetch_sift1m.sh                 # ~1 GB, not committed
pip install -e ".[bench]"                              # faiss-cpu + matplotlib
python benchmarks/sift1m/run_sweep.py \
  --dataset-dir benchmarks/sift1m/data/sift \
  --snapshot benchmarks/sift1m/data/index.hnsw --faiss --write-docs
```

The table and plot below are regenerated in place by `run_sweep.py --write-docs`
(it rewrites everything between the two HTML markers). The deliverable is the
Pareto curve (recall@10 vs QPS) on the same hardware, plus an honest write-up of
where go-hnsw lands relative to Faiss and why.

<!-- SIFT_TABLE_START -->

| Index | M | efC | efSearch | Recall@10 | QPS (1 thread) | P50 (ms) | P99 (ms) | Build (s) | RSS (MB) | Recover P50 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| faiss | 16 | 200 | 16 | 0.8131 | 14498 | 0.067 | 0.122 | 449.5 | 1372 | 0.00 |
| faiss | 16 | 400 | 16 | 0.8185 | 13933 | 0.069 | 0.127 | 860.1 | 1408 | 0.00 |
| faiss | 32 | 200 | 16 | 0.8606 | 11342 | 0.087 | 0.155 | 547.5 | 1664 | 0.00 |
| faiss | 32 | 400 | 16 | 0.8738 | 10763 | 0.092 | 0.163 | 1033.8 | 1687 | 0.00 |
| faiss | 16 | 200 | 32 | 0.9122 | 9100 | 0.110 | 0.183 | 449.5 | 1372 | 0.00 |
| faiss | 16 | 400 | 32 | 0.9184 | 8623 | 0.114 | 0.200 | 860.1 | 1408 | 0.00 |
| faiss | 32 | 200 | 32 | 0.9423 | 6877 | 0.146 | 0.234 | 547.5 | 1664 | 0.00 |
| faiss | 32 | 400 | 32 | 0.9515 | 6550 | 0.156 | 0.241 | 1033.8 | 1687 | 0.00 |
| faiss | 16 | 200 | 64 | 0.9683 | 5141 | 0.197 | 0.299 | 449.5 | 1372 | 0.00 |
| faiss | 16 | 400 | 64 | 0.9723 | 4976 | 0.204 | 0.297 | 860.1 | 1408 | 0.00 |
| faiss | 32 | 200 | 64 | 0.9819 | 3979 | 0.255 | 0.402 | 547.5 | 1664 | 0.00 |
| faiss | 32 | 400 | 64 | 0.9868 | 3727 | 0.272 | 0.412 | 1033.8 | 1687 | 0.00 |
| faiss | 16 | 200 | 128 | 0.9909 | 2765 | 0.369 | 0.547 | 449.5 | 1372 | 0.00 |
| faiss | 16 | 400 | 128 | 0.9931 | 2672 | 0.381 | 0.556 | 860.1 | 1408 | 0.00 |
| faiss | 32 | 200 | 128 | 0.9952 | 2182 | 0.470 | 0.700 | 547.5 | 1664 | 0.00 |
| faiss | 32 | 400 | 128 | 0.9970 | 2062 | 0.497 | 0.730 | 1033.8 | 1687 | 0.00 |
| faiss | 16 | 200 | 256 | 0.9976 | 1479 | 0.690 | 0.966 | 449.5 | 1372 | 0.00 |
| faiss | 16 | 400 | 256 | 0.9982 | 1418 | 0.722 | 1.002 | 860.1 | 1408 | 0.00 |
| faiss | 32 | 200 | 256 | 0.9988 | 1174 | 0.880 | 1.251 | 547.5 | 1664 | 0.00 |
| faiss | 32 | 400 | 256 | 0.9992 | 1111 | 0.929 | 1.327 | 1033.8 | 1687 | 0.00 |
| go-hnsw | 16 | 200 | 16 | 0.8249 | 5403 | 0.176 | 0.407 | 1316.8 | 2672 | 11.67 |
| go-hnsw | 16 | 400 | 16 | 0.8265 | 5118 | 0.188 | 0.358 | 2392.9 | 3770 | 11.90 |
| go-hnsw | 16 | 200 | 32 | 0.9159 | 3476 | 0.285 | 0.443 | 1316.8 | 2672 | 11.67 |
| go-hnsw | 16 | 400 | 32 | 0.9180 | 3220 | 0.305 | 0.494 | 2392.9 | 3770 | 11.90 |
| go-hnsw | 16 | 200 | 64 | 0.9684 | 2020 | 0.502 | 0.713 | 1316.8 | 2672 | 11.67 |
| go-hnsw | 16 | 400 | 64 | 0.9700 | 1875 | 0.532 | 0.815 | 2392.9 | 3770 | 11.90 |
| go-hnsw | 32 | 200 | 64 | 0.9892 | 1207 | 0.853 | 1.230 | 2630.7 | 3772 | 13.03 |
| go-hnsw | 16 | 200 | 128 | 0.9908 | 1105 | 0.917 | 1.343 | 1316.8 | 2672 | 11.67 |
| go-hnsw | 32 | 400 | 64 | 0.9909 | 1102 | 0.930 | 1.344 | 4105.2 | 4001 | 12.21 |
| go-hnsw | 16 | 400 | 128 | 0.9913 | 1043 | 0.966 | 1.465 | 2392.9 | 3770 | 11.90 |
| go-hnsw | 32 | 200 | 128 | 0.9973 | 660 | 1.557 | 2.306 | 2630.7 | 3772 | 13.03 |
| go-hnsw | 32 | 400 | 128 | 0.9980 | 624 | 1.654 | 2.480 | 4105.2 | 4001 | 12.21 |
| go-hnsw | 32 | 200 | 256 | 0.9990 | 369 | 2.772 | 4.310 | 2630.7 | 3772 | 13.03 |
| go-hnsw | 32 | 400 | 256 | 0.9992 | 358 | 2.886 | 4.285 | 4105.2 | 4001 | 12.21 |

![SIFT-1M recall vs QPS Pareto frontier](../../benchmarks/sift1m/results/2026-06-29-sift-pareto.png)

<!-- SIFT_TABLE_END -->

### Findings (honest read)

* **Recover P50 = 11.7–13.0 ms** across every 1M × 128 config — comfortably inside the `< 200 ms` Phase 4 exit budget. The mmap snapshot aliases the 512 MB vector arena zero-copy, so reload cost is parsing the small CSR/id arrays, not an O(n) re-insert. This is the metric Phase 4 was built to hit, and it does so with ~15× headroom.
* **Recall tracks Faiss closely.** At matched `(M, efC, efSearch)` go-hnsw and Faiss land within ~0.5 recall points (e.g. `M=32, efC=400, efSearch=256`: go-hnsw 0.9992 vs Faiss 0.9992; `efSearch=64`: 0.9909 vs 0.9868). The Algorithm-4 heuristic graph is correct.
* **Faiss is faster on the recall-vs-QPS frontier — by ~2.5–3×.** At ~0.99 recall Faiss does ~2.7k QPS (single thread) where go-hnsw does ~1.0–1.1k. So the *global* Pareto frontier is entirely Faiss; the table above therefore plots each index's *own* frontier side by side rather than the global one (which would hide go-hnsw). The gap is expected: Faiss-HNSWFlat has SIMD distance kernels and a hand-tuned search loop; go-hnsw is plain Go with a scalar distance function and no prefetching. Closing it (SIMD / batched distance, lock-free read path) is Phase 5 performance work, not a Phase 4 deliverable.
* **Build is also ~3–6× slower** (go-hnsw 1317–4105 s vs Faiss 450–1034 s for the same configs), same root cause. Build speed is not a Phase 4 exit metric.
* **RSS** is higher for go-hnsw (2.7–4.0 GB vs Faiss 1.4–1.7 GB) because the in-memory build keeps `[][]uint32` adjacency; the on-disk snapshot is the compact CSR form, and the served (recovered) index aliases vectors via mmap.

Bottom line: Phase 4 set out to make go-hnsw *persistent and benchmarkable*, and to prove fast reload — both done, with an apples-to-apples Faiss baseline that honestly shows go-hnsw matching recall but trailing on throughput.

## 2. Cross-File QA benchmark — 200 questions

* Set: `benchmarks/cross_file_qa/questions.jsonl` (Phase 3 ships 50, Phase 6 the full 200).
* Repos: documented per row.
* Metrics: citation grounding (exact), Ragas `answer_correctness`, Ragas `faithfulness`.
* Models (Phase 4 weekly run): A (router) and B (answer) come from `LLM_MODEL`/`ROUTER_MODEL`. The local-default-Ollama setup (DD-014) is for development; the eval-gate weekly cron runs against a hosted A/B pair such as `openai/gpt-4o-mini` (router) and `anthropic/claude-sonnet-4` (answer), with C (judge) held out (e.g. `openai/gpt-4o`).

| Route | Subset (n) | Citation grounding | Ragas correctness | Ragas faithfulness |
| --- | --- | --- | --- | --- |
| graph | deterministic graph (80) | _pending_ | _pending_ | _pending_ |
| community | module aggregation (40) | _pending_ | _pending_ | _pending_ |
| hybrid | semantic (60) | _pending_ | _pending_ | _pending_ |
| auto | all (200) | _pending_ | _pending_ | _pending_ |
| hybrid-only | all (200) — baseline | _pending_ | _pending_ | _pending_ |

The headline number is `auto - hybrid-only` on the 200-question total: the lift from routing to the right index instead of stuffing every question into vector RAG.

## 3. Latency budget — `/ask` end-to-end

Measured on the GitHub App round-trip (webhook → answer comment), broken down by stage. Filled in during Phase 4.

| Stage | P50 (ms) | P99 (ms) |
| --- | --- | --- |
| webhook receive | _pending_ | _pending_ |
| router classify | _pending_ | _pending_ |
| graph adjacency | _pending_ | _pending_ |
| HNSW search | _pending_ | _pending_ |
| BM25 search | _pending_ | _pending_ |
| reranker | _pending_ | _pending_ |
| LLM completion | _pending_ | _pending_ |
| markdown render | _pending_ | _pending_ |
| **total** | _pending_ | _pending_ |

## 4. Indexing throughput

Filled in during Phase 6.

| Repo (kLOC) | Files | Chunks | Wall time | Chunks/s | Peak RSS |
| --- | --- | --- | --- | --- | --- |
| _pending_ | — | — | — | — | — |
