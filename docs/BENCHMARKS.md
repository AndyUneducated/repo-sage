# Benchmarks

This file is the single source of truth for any number we publish about RepoSage. Numbers land here as they are produced; the README links here rather than embedding stale figures.

## 1. SIFT-1M ANN benchmark — go-hnsw vs Faiss

* Dataset: SIFT-1M (1M × 128-d, 10k queries, ground-truth top-100, L2 metric).
* Configuration sweep: `M ∈ {8, 16, 32}` × `efC ∈ {100, 200, 400}` × `efSearch ∈ {16, 32, 64, 128, 256}`.
* Hardware: documented per row in `benchmarks/sift1m/results/<timestamp>.csv`.

| Index | M | efC | efSearch | Recall@10 | QPS (1 thread) | P50 (ms) | P99 (ms) | Build (s) | RSS (MB) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| go-hnsw | — | — | — | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| faiss | 16 | 200 | 64 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

> Filled in during Phase 5. The deliverable is the Pareto curve (recall@10 vs QPS) on the same hardware, plus an honest write-up of where go-hnsw lands relative to Faiss and why.

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
