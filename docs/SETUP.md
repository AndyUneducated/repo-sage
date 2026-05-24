# Local Setup

This is the practical day-to-day setup; the README has the high-level summary.

## 1. Prerequisites

| Tool | Minimum version | Notes |
| --- | --- | --- |
| Python | 3.11 | 3.12 recommended; `uv` is the fastest installer. |
| Go | 1.22 | Needed for `go-hnsw`. |
| Make | any | Most workflows are wrapped behind `make` targets. |
| Git | 2.30+ | LFS not required. |
| Ollama | 0.3+ | Default LLM provider; `make bench-rag` and `reposage ask` expect it on `localhost:11434` (DD-014). Skip if you only do offline mock runs. |
| Docker | optional | Only needed for the full stack via `docker-compose up`. |
| OpenTelemetry | optional | If you want to inspect traces locally; otherwise spans are silently dropped. |

## 2. First-time install

```bash
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
cp .env.example .env       # defaults to local Ollama; edit only if you need a hosted provider

make install-dev           # python deps + pre-commit
make hnsw-build            # builds bin/hnsw-server and bin/hnsw-bench

# Pull the default LLM (skip if you'll only ever run REPOSAGE_LLM_PROVIDER=mock)
ollama pull qwen2.5-coder:7b
```

## 3. Running the dev stack

Two terminals, no docker:

```bash
# terminal 1 — dense vector store
make hnsw-run              # uses ./data/reposage.db, model=BAAI/bge-en-v1.5, dim=768

# terminal 2 — HTTP service
make dev                   # uvicorn on :8000, autoreload on
```

`hnsw-run` cold-loads embeddings out of the SQLite index (built by
`reposage index`). The first start after a fresh `index` rebuilds the
HNSW graph in-memory; from then on the server stays up.

Or one terminal with docker:

```bash
docker-compose up --build
```

### LLM provider ladder (DD-014)

Three rungs, lowest cost first:

| Mode | How to enable | When to use |
| --- | --- | --- |
| **mock** (offline) | `REPOSAGE_LLM_PROVIDER=mock` | Iterating on plumbing without burning tokens; CI without secrets. Uses `HashEmbedder` + `MockLLMClient` + `MockReranker`; deterministic. |
| **ollama** (default, local) | `ollama serve` + `ollama pull qwen2.5-coder:7b` | Local development; full `/ask` quality without an API key. The `LLM_MODEL=ollama_chat/<name>` setting + `OLLAMA_API_BASE` in `.env` are wired by default. |
| **hosted** (OpenAI / Anthropic) | Set `LLM_MODEL=openai/gpt-4o-mini` (or `anthropic/...`) **and** the matching API key in `.env` | Production-grade quality runs and the weekly `eval-gate` workflow. |

The runtime is provider-agnostic via LiteLLM, so there is no code to
change when you switch — only `.env`.

`make bench-rag` defaults to whatever `LLM_MODEL` points at and pings
Ollama up front; pass `REPOSAGE_RAG_LLM=mock` to bypass for a quick
plumbing check.

## 4. Indexing your first repo

```bash
python -m reposage.cli index --repo /path/to/your/repo
```

Phase 1 wires up Python end-to-end. Output looks like::

```
Indexing /path/to/your/repo (langs=python, force=False)
   Index manifest for repo
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ metric            ┃ value ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ files seen        │  1329 │
│ python files      │  1321 │
│ unsupported files │     0 │
│ parse errors      │     0 │
│ chunks            │  2592 │
│ symbols (nodes)   │  3905 │
│ edges             │ 16307 │
│ elapsed (s)       │ 0.570 │
└───────────────────┴───────┘
```

TypeScript / JavaScript / Go files are *parse-validated only* in Phase 1 and recorded in `file_meta` with `parse_status='unsupported'`. Phase 2 starts pushing chunks to HNSW; later phases add the resolver for the other languages.

## 5. Asking questions

Phase 2 wires up three routes:

```bash
# Symbolic graph route (no LLM, no embedder)
python -m reposage.cli ask "where is User.login called?" --route graph

# Hybrid RAG (HNSW + BM25 + reranker + LLM, with citation grounding)
python -m reposage.cli ask "how is the session timeout configured?" --route hybrid

# Auto: graph fast-path when a symbol is in the question, hybrid otherwise.
python -m reposage.cli ask "explain authentication" --route auto
```

The CLI defaults to a `LocalDenseIndex` built from the local SQLite
embeddings (no Go binary required). To talk to `hnsw-run` instead, set
`REPOSAGE_DENSE=grpc`.

Sample output::

```
Q: where is User.login called?

pkg.auth.users.User.login  (pkg/auth/users.py:6)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ caller                          ┃ path:line               ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ pkg.api.routes.login_route      │ pkg/api/routes.py:22    │
│ pkg.auth.sessions.Session.open  │ pkg/auth/sessions.py:13 │
└─────────────────────────────────┴─────────────────────────┘
```

## 6. Running the test suite

```bash
make test           # python unit + integration tests (skips gRPC + Ollama; bench-rag uses mock)
make test-grpc      # gRPC integration test against a freshly built hnsw-server
make test-ollama    # real-LLM smoke test against your local Ollama (`ollama serve` first)
make hnsw-test      # go unit tests with -race
make bench-graph    # 30-question graph QA benchmark (precision >= 0.90)
make bench-rag      # 20-question hybrid RAG benchmark — uses your configured LLM (Ollama by default)
REPOSAGE_RAG_LLM=mock make bench-rag    # offline mode; useful in CI / forks without Ollama
make precommit      # full lint + format check
```

The 50 kLOC indexing performance check expects you to point at a real Python checkout::

```bash
REPOSAGE_LARGE_REPO=/path/to/big/python/repo make bench-graph LARGE=1
```

## 7. Troubleshooting

* **`pre-commit` complains about gofmt** — run `make fmt` and stage the result.
* **Embedder is slow on Apple Silicon** — set `EMBED_DEVICE=mps` in `.env`.
* **`go test` fails to download modules** — set `GOPROXY=https://proxy.golang.org,direct`.
