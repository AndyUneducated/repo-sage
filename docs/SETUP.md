# Local Setup

This is the practical day-to-day setup; the README has the high-level summary.

## 1. Prerequisites

| Tool | Minimum version | Notes |
| --- | --- | --- |
| Python | 3.11 | 3.12 recommended; `uv` is the fastest installer. |
| Go | 1.22 | Needed for `go-hnsw`. |
| Make | any | Most workflows are wrapped behind `make` targets. |
| Git | 2.30+ | LFS not required. |
| Docker | optional | Only needed for the full stack via `docker-compose up`. |
| OpenTelemetry | optional | If you want to inspect traces locally; otherwise spans are silently dropped. |

## 2. First-time install

```bash
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
cp .env.example .env       # fill in API keys

make install-dev           # python deps + pre-commit
make hnsw-build            # builds bin/hnsw-server and bin/hnsw-bench
```

## 3. Running the dev stack

Two terminals, no docker:

```bash
# terminal 1
./go-hnsw/bin/hnsw-server --addr=127.0.0.1:50051 --data-dir=./data/hnsw

# terminal 2
make dev                   # uvicorn on :8000, autoreload on
```

Or one terminal with docker:

```bash
docker-compose up --build
```

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

Phase 1 only serves the deterministic `graph` route — the answering LLM and `/ask` HTTP endpoint land in Phase 2.

```bash
# Symbolic graph route (no LLM, no embedder)
python -m reposage.cli ask "where is User.login called?" --route graph
```

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
make test           # python unit + integration tests (incl. graph-bench gate)
make hnsw-test      # go unit tests with -race
make bench-graph    # 30-question graph QA benchmark (precision >= 0.90)
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
