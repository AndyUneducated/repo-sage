# Contributing

Thanks for the interest. RepoSage is moving quickly through the phases in [`docs/ROADMAP.md`](docs/ROADMAP.md) — please read that file before opening a feature PR so we are not duplicating work that is already designed for a later phase.

We especially welcome contributions in these areas:

- More ANN benchmarks for `go-hnsw/` (high-dimensional / large-scale / different distributions).
- New cross-file QA questions in `benchmarks/cross_file_qa/` (Rust, Java, Kotlin, …).
- Better GraphRAG community summaries (Leiden tuning, summary prompts).
- New LLM providers via the LiteLLM abstraction in `reposage.llm`.

## 1. Local setup

```bash
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
make install-dev          # dev + eval deps; runs pre-commit install

make hnsw-build           # build go-hnsw server + bench binaries
make dev                  # FastAPI on :8000 (see docs/SETUP.md for the full stack)
```

Requirements:

- Python 3.12+ ([`uv`](https://docs.astral.sh/uv/) recommended)
- Go 1.22+
- SQLite (system default is fine)
- Local LLM: optional Ollama, or a hosted LiteLLM-compatible provider in `.env`

Full day-to-day setup (profiles, indexing, asking questions) lives in [`docs/SETUP.md`](docs/SETUP.md).

## 2. Workflow

1. Pick (or open) an issue tagged with the relevant phase milestone.
2. Branch off `main`. Branch naming: `<phase>/<short-slug>` (e.g. `phase-2/rrf-fusion`).
3. Before pushing, run the same checks CI runs:

   ```bash
   make lint        # ruff + go vet + gofmt
   make typecheck   # mypy strict
   make test        # pytest + go test (mock profile; skips gRPC / Ollama marks)
   ```

   Or run everything at once: `make precommit`.

4. Open a PR using the template. If you touched retrieval, attach a `make bench-qa` or `make bench-rag` delta vs baseline.

`pre-commit` hooks are configured at the repo root — `make install-dev` installs them; you can also run `pre-commit install` manually.

## 3. Code conventions

- **Do not commit generated index artefacts.** `*.db`, `*.idx`, and embedding caches are in `.gitignore`.
- **Cross-process / cross-language boundaries need contracts.** Changes to the Python ↔ `go-hnsw` gRPC protocol must update stubs and tests on both sides (`make proto-gen`).
- **Major design choices** go in [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md) (append-only ADR style). Phase progress belongs in [`docs/ROADMAP.md`](docs/ROADMAP.md).
- **Benchmark numbers** must be reproducible: document hardware, parameters, and random seeds in [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md).

### Code style

* Python: ruff + mypy strict. New public APIs must have type hints.
* Go: `gofmt` + `go vet`. Public API changes need doc comments.
* Tests: prefer fixture-driven; mark slow tests with `@pytest.mark.slow`.

## 4. Commit messages

- Short English subject lines; conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`).
- One logical change per commit; split cross-module work when it helps review.
- PR description: **why** / **what** / **how to test**.

## 5. CI must pass

Every PR runs:

- `ci-python.yml` — Ruff + mypy + pytest
- `ci-go.yml` — `go vet` + `go test ./...`
- `lint.yml` — pre-commit

If your change moves ANN or cross-file QA numbers, paste before/after in the PR description.

## 6. Adding a benchmark question

* Append to `benchmarks/cross_file_qa/questions.jsonl`.
* Provide a reference answer and at least one reference citation `(repo, path, start, end)`.
* Mark the bucket (`graph` / `community` / `hybrid` / `negative`) so the eval harness can score per-route.

## 7. Larger proposals

If you plan to add a new index type (beyond HNSW / BM25 / Symbol Graph / GraphRAG), swap the embedding model, or rewrite the query router, **open an issue first** so we can align before you invest in a large PR.

## 8. Security

Do not file security issues publicly. Email the maintainers instead; encryption keys can be exchanged on request.
