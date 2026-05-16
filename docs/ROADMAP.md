# Roadmap

The project is organised as **seven phases**. Each phase has an explicit *deliverable*, a *demo* you can record, and an *exit metric* you can put in a sentence. Phases are sized to take roughly one focused calendar week each, with the bigger systems phases (HNSW, GraphRAG) eating two.

> **Operating principle**: every phase ends with something demoable and a CI signal. We never hold features behind a giant integration step.

| Phase | Theme | Duration | Exit metric |
| --- | --- | --- | --- |
| 0 | Skeleton & CI | 0.5 wk | Repo green on `main` with empty stubs; CI badges live; one-liner local bring-up. |
| 1 | Indexer v1: tree-sitter + symbol graph | 1.0 wk | `reposage index <repo>` populates SQLite; "where is X called" works against a 10 kLOC fixture. |
| 2 | Retrieval v1: hybrid RAG end-to-end | 1.5 wk | `/ask` returns cited answers; HNSW + BM25 + RRF + reranker wired; latency P50 < 1.5 s on a small repo. |
| 3 | GraphRAG (Leiden + summaries) | 1.5 wk | Module-level questions answered; first 50 of 200 benchmark questions live; +X% over hybrid baseline. |
| 4 | GitHub App | 1.0 wk | Live on a public OSS repo; @-mention → cited PR comment within 30 s. |
| 5 | go-hnsw v2: persistence + benchmark | 1.5 wk | mmap snapshots; SIFT-1M Pareto curve vs Faiss in `docs/BENCHMARKS.md`. |
| 6 | Hardening: eval gate, OTel, perf | 1.0 wk | Eval-gate workflow blocks regressions; full 200-question benchmark; OTel traces flowing. |
| 7 | Stretch: Tantivy / TS+Go grammars | flex | BM25 swap; multi-language indexing on a real polyglot repo; blog post drafted. |

---

## Phase 0 — Skeleton & CI

**Why first**: keep the repo permanently green so every later phase is a forward step, not a recovery.

* Project layout (`reposage/`, `go-hnsw/`, `benchmarks/`, `docs/`).
* `pyproject.toml` with ruff + mypy strict + pytest.
* `go.mod` for the Go HNSW module; `gofmt -l` enforced in CI.
* GitHub Actions workflows: `ci-python`, `ci-go`, `lint`, `eval-gate` (skipped without label).
* README with badges; LICENSE (Apache 2.0); `.env.example`.
* One smoke test (`/healthz`), one Go unit test (`Cosine`).

**Demo**: `git clone && make install-dev && make test` is green.
**Exit metric**: all four CI workflows green; coverage publishing wired (zero is fine).

---

## Phase 1 — Indexer v1: tree-sitter + symbol graph

**Goal**: turn a checked-out repo into queryable symbol-graph rows.

* tree-sitter parser wrapper for Python first, then TypeScript and Go.
* AST-driven chunker with max-lines + overlap.
* Symbol graph extraction (`def`, `call`, `inherit`, `import`).
* SQLite schema + adjacency-store implementation.
* `reposage index` CLI; `reposage ask --route graph` over a fixture repo.

**Demo**: index `pallets/flask` (or a local fixture) and answer "where is `Flask.route` called?" with a list of `file:line` links.
**Exit metric**: ≥ 90% precision on a 30-question hand-graded sample of graph queries against the fixture; index pass < 60 s for 50 kLOC.

---

## Phase 2 — Retrieval v1: hybrid RAG end-to-end

**Goal**: a working `/ask` endpoint with citations.

* Embedder (`bge-en-v1.5`, lazy load).
* `go-hnsw` insert + search v1 (in-memory, single-threaded, Algorithm 1 / 5 from the paper). gRPC server in `cmd/server`.
* BM25 index using rank-bm25.
* `HybridRetriever` with RRF; cross-encoder reranker.
* `QueryRouter` heuristic + LLM fallback.
* LiteLLM client; prompt templates (`reposage/llm/prompts.py`).
* Citation grounding check + drop-and-regenerate fallback.

**Demo**: ask "How is the session timeout configured?" against an OSS repo; answer cites the right two files.
**Exit metric**: P50 end-to-end latency < 1.5 s on a 50 kLOC repo; manual quality bar of "would I send this answer to a teammate" on 20 questions.

---

## Phase 3 — GraphRAG: Leiden + community summaries

**Goal**: answer module-level questions that hybrid retrieval cannot.

* igraph + leidenalg-driven `CommunityDetector`; hierarchical (multi-level) partition.
* `CommunitySummarizer` using a cheaper LLM; result cached in SQLite.
* `community` route in `QueryRouter`.
* First 50 questions of the cross-file benchmark, with reference answers.
* Run benchmark: `community` route vs `hybrid` route on the 40 module-aggregation questions.

**Demo**: ask "How do auth and billing modules interact?" on a real OSS repo; answer cites multiple modules and includes a community summary.
**Exit metric**: ≥ 25% absolute lift in Ragas `answer_correctness` on the 40 module-aggregation questions versus hybrid-only.

---

## Phase 4 — GitHub App deployment

**Goal**: real users (us, then anyone) can install RepoSage on a public repo.

* GitHub App registration; private key + webhook secret in `.env`.
* HMAC verification + JWT minting + installation token caching.
* `@reposage` command parser; comment-thread lifecycle.
* Webhook handler that dispatches to the existing `/ask` flow.
* Markdown citation renderer with permalinks (`#L42-L57`).
* Long-running indexing kicked off by `push` events.

**Demo**: install on a public OSS repo, open a PR, comment `@reposage where does the request enter routing?`, get a cited answer back within 30 s.
**Exit metric**: round-trip on the demo repo < 30 s P95; signature verification green; rate-limit handling tested.

---

## Phase 5 — go-hnsw v2: persistence + SIFT-1M benchmark

**Goal**: turn the HNSW module from "works in memory" into a serious, benchmarkable artefact.

* mmap snapshot/recover with the CSR adjacency format described in `docs/ARCHITECTURE.md`.
* Heuristic neighbour selection (Algorithm 4) and proper level multiplier sampling.
* Atomic snapshot writes (`tmp + rename`).
* SIFT-1M benchmark in `cmd/bench`: build / recall@10 / QPS / P50 / P99 / RSS.
* Sweep driver in `benchmarks/sift1m/run_sweep.py`.
* Recall-vs-QPS Pareto plot committed to `docs/BENCHMARKS.md`.
* Faiss baseline run on the same hardware.

**Demo**: open `docs/BENCHMARKS.md`; the plot loads with two curves (go-hnsw, Faiss-HNSWFlat) and an honest commentary on the gap.
**Exit metric**: Pareto curve published; reload-from-snapshot P50 < 200 ms for 1M × 128-d.

---

## Phase 6 — Hardening: eval gate, OTel, perf

**Goal**: every change is measured before it ships.

* Full 200 questions in `benchmarks/cross_file_qa/questions.jsonl` (Python + TS + Go).
* `eval-gate` GitHub Action becomes a required check on PRs labelled `run-eval`.
* OTel traces from indexing + serving exported to a local Tempo / Jaeger; commented dashboards in `docs/`.
* Performance pass: profile the hot path, batch HNSW upserts, parallelise community summarisation.
* Concurrency in `go-hnsw`: per-layer RWMutex, lock-free read path.

**Demo**: open a PR that regresses retrieval recall; eval-gate blocks it.
**Exit metric**: eval-gate runs in < 10 minutes; P99 indexing throughput ≥ 1k chunks/s on a 4-core laptop.

---

## Phase 7 — Stretch goals (any order)

These are individually shippable improvements; pick whichever produces the biggest delta on a current pain point.

* **Tantivy BM25**: swap rank-bm25 for Tantivy via a small Rust → Python bridge; expect ~10× indexing throughput.
* **More languages**: add Java and Rust grammars; verify symbol-graph queries still work.
* **Incremental re-indexing**: only re-parse changed files on `push`; reuse symbol-graph rows for untouched files.
* **Caching layer**: per-question cache keyed on `(repo_sha, question)`; serve repeats in < 100 ms.
* **Public blog post**: "What we learned writing HNSW from scratch in Go", with the SIFT-1M numbers.

---

## Tracking progress

* Each phase is a milestone in the issue tracker (`Phase 0` … `Phase 7`).
* Phase exit metrics live in this file *and* in their milestone description; both are updated when reality differs.
* `docs/BENCHMARKS.md` is the single source of truth for any number we publish in the README.
