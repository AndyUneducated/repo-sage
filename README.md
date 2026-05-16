# RepoSage

> Repository-level code Q&A with a **dual-index** design (Symbol Graph + GraphRAG) and a **from-scratch Go HNSW** vector store. Ships as a GitHub App.

[![CI · Python](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-python.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-python.yml)
[![CI · Go HNSW](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-go.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-go.yml)
[![Lint](https://github.com/AndyUneducated/repo-sage/actions/workflows/lint.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/lint.yml)
[![codecov](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](https://codecov.io)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Go 1.22+](https://img.shields.io/badge/go-1.22+-00ADD8.svg?logo=go&logoColor=white)](https://go.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![tree-sitter](https://img.shields.io/badge/tree--sitter-AST-green.svg)](https://tree-sitter.github.io/)
[![SQLite](https://img.shields.io/badge/SQLite-symbol_graph-003B57.svg?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![HNSW](https://img.shields.io/badge/HNSW-from_scratch-orange.svg)](./go-hnsw)
[![GraphRAG](https://img.shields.io/badge/GraphRAG-Leiden-purple.svg)](https://microsoft.github.io/graphrag/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-traced-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg?logo=pre-commit)](https://pre-commit.com/)

---

## Why RepoSage

A new engineer joining a 500k-line repo asks three kinds of questions, and a single mechanism cannot answer all of them well:

| Question type | Real need | Why pure vector RAG fails |
| --- | --- | --- |
| *"Where is `User.login()` called?"* | Deterministic graph lookup | Top-k similarity may miss reflective / cross-file edges; this is fact. |
| *"How do I change the session timeout?"* | Cross-file reasoning over snippets | Needs hybrid retrieval + reranking, not raw embeddings. |
| *"How do the auth and billing modules talk?"* | Module-scale aggregation | 5–10 chunks cannot describe a module boundary. |

RepoSage routes each question to the right index instead of brute-forcing one.

## Architecture (one picture)

```mermaid
graph LR
  subgraph Indexer
    P[tree-sitter Parser] --> C[Chunker]
    C --> E[Embedder bge-en-v1.5]
    P --> S[Symbol Graph<br/>def · call · inherit · import]
    S --> G[GraphRAG<br/>Leiden community detection]
  end
  subgraph Stores
    HN[(go-hnsw<br/>self-built, mmap)]
    BM[(BM25)]
    SG[(SQLite<br/>symbol graph)]
    SU[(Community summaries)]
  end
  E --> HN
  C --> BM
  S --> SG
  G --> SU
  subgraph Online
    Q[Query Router] --> HY[Hybrid Retrieval<br/>HNSW + BM25 + RRF + reranker]
    Q --> SQ[Symbol-graph adjacency]
    Q --> CS[Community summary]
    HY & SQ & CS --> L[LLM] --> CITE[file:line citations] --> BOT[GitHub App reply]
  end
  HN -.- HY
  BM -.- HY
  SG -.- SQ
  SU -.- CS
```

A long-form architecture write-up lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Phase-by-phase delivery plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).
Design trade-offs: [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md).
Benchmarks (HNSW vs Faiss on SIFT-1M, 200-question cross-file QA): [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

## What is novel

1. **`go-hnsw/` — HNSW from scratch in Go.** Implementation of Malkov 2018 with `mmap` persistence; benchmarked against Faiss on SIFT-1M with QPS / RAM / P99-latency reported across `M`, `efConstruction`, `efSearch`. Lives as an independently consumable Go module.
2. **Dual-index retrieval.** Symbol Graph (deterministic) + GraphRAG community summaries (aggregation) + Hybrid vector/BM25 (semantic fallback). A lightweight query router picks the path. We measure +30% answer accuracy versus a vector-only baseline on a self-built 200-question cross-file benchmark.
3. **Self-built evaluation harness.** 200 hand-curated cross-file questions across Python / TypeScript / Go repos, scored with Ragas + custom citation-grounding checks, and wired into CI as a regression gate.
4. **Read half of a code-intelligence stack.** Pairs with a sister project that does the *write* half (refactor / mutation), so the two systems share a common index format.

## Quick start

> Detailed setup, including model downloads and tree-sitter grammars, is in `docs/SETUP.md` (added in Phase 1).

```bash
# 1. Clone & install Python deps (uv recommended)
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
make install

# 2. Build the Go HNSW server
make hnsw-build

# 3. Run dev stack (FastAPI + go-hnsw + SQLite)
make dev

# 4. Index a repo
python -m reposage.cli index --repo /path/to/your/repo

# 5. Ask a question
python -m reposage.cli ask "where is User.login called?"
```

## Repository layout

```
repo-sage/
├── reposage/               # Python service (FastAPI, indexer, retrieval, bot)
│   ├── api/                # FastAPI routes & schemas
│   ├── indexer/            # tree-sitter parsing, chunking, embedding, symbol graph
│   │   └── graphrag/       # Leiden community detection + LLM summarisation
│   ├── retrieval/          # Hybrid retriever, query router, reranker
│   ├── storage/            # SQLite symbol graph, community store
│   ├── bot/                # GitHub App webhook + citation builder
│   ├── llm/                # LiteLLM-backed multi-provider client
│   └── observability/      # OpenTelemetry wiring
├── go-hnsw/                # Self-built HNSW Go module (independently OSS-able)
│   ├── cmd/server/         # gRPC/HTTP server consumed by the Python side
│   └── cmd/bench/          # SIFT-1M benchmark harness
├── benchmarks/
│   ├── cross_file_qa/      # 200-question cross-file QA benchmark + Ragas
│   └── sift1m/             # ANN benchmark (HNSW vs Faiss)
├── docs/                   # Architecture, roadmap, decisions, benchmarks
├── scripts/                # One-shot dev scripts
└── .github/workflows/      # CI: Python, Go, lint, eval-gate
```

## Status

This project is under active development. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the phase-by-phase plan and the issue tracker for open milestones.

## License

Apache 2.0. See [`LICENSE`](./LICENSE).
