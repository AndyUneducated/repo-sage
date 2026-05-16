# Contributing

Thanks for the interest. The project is moving fast through the phases listed in [`ROADMAP.md`](./ROADMAP.md) — please look there before opening a feature PR so we are not duplicating Phase N+1 work that is already designed.

## Workflow

1. Pick (or open) an issue tagged with the relevant phase milestone.
2. Branch off `main`. Branch naming: `<phase>/<short-slug>` (e.g. `phase-2/rrf-fusion`).
3. Run `make precommit` before pushing.
4. Open a PR using the template; include a benchmark delta if you touched retrieval.

## Code style

* Python: ruff + mypy strict. New public APIs must have type hints.
* Go: `gofmt` + `go vet`. Public API changes need doc comments.
* Tests: prefer fixture-driven; mark slow tests with `@pytest.mark.slow`.

## Adding a benchmark question

* Append to `benchmarks/cross_file_qa/questions.jsonl`.
* Provide a reference answer and at least one reference citation `(repo, path, start, end)`.
* Mark the bucket (`graph` / `community` / `hybrid` / `negative`) so the eval harness can score per-route.

## Reporting a security issue

Email rather than filing a public issue. Encryption keys can be exchanged on request.
