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
python -m reposage.cli index --repo /path/to/your/repo --languages python,typescript
```

The first run downloads embedding + reranker weights into `~/.cache/huggingface/`.

## 5. Asking questions

```bash
python -m reposage.cli ask "where is User.login called?"
# or hit /ask directly
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"where is User.login called?","top_k":5}' | jq
```

## 6. Running the test suite

```bash
make test           # python unit tests
make hnsw-test      # go unit tests with -race
make precommit      # full lint + format check
```

## 7. Troubleshooting

* **`pre-commit` complains about gofmt** — run `make fmt` and stage the result.
* **Embedder is slow on Apple Silicon** — set `EMBED_DEVICE=mps` in `.env`.
* **`go test` fails to download modules** — set `GOPROXY=https://proxy.golang.org,direct`.
