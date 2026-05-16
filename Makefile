.PHONY: help install install-dev fmt lint typecheck test cov dev hnsw-build hnsw-test hnsw-bench bench-qa bench-sift clean precommit docker

PYTHON ?= python3
UV     ?= uv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- Python ----------

install: ## Install runtime deps with uv (or pip fallback)
	@if command -v $(UV) >/dev/null 2>&1; then \
		$(UV) pip install -e .; \
	else \
		$(PYTHON) -m pip install -e .; \
	fi

install-dev: ## Install dev + eval deps
	@if command -v $(UV) >/dev/null 2>&1; then \
		$(UV) pip install -e ".[dev,eval]"; \
	else \
		$(PYTHON) -m pip install -e ".[dev,eval]"; \
	fi
	pre-commit install

fmt: ## Format code (ruff)
	ruff format reposage tests benchmarks
	ruff check --fix reposage tests benchmarks

lint: ## Lint Python + Go
	ruff check reposage tests benchmarks
	ruff format --check reposage tests benchmarks
	$(MAKE) -C go-hnsw lint

typecheck: ## mypy strict
	mypy reposage

test: ## Run unit tests
	pytest -q

cov: ## Run tests with coverage
	pytest --cov=reposage --cov-report=term-missing --cov-report=xml

dev: ## Run FastAPI in reload mode
	uvicorn reposage.api.main:app --reload --host 0.0.0.0 --port 8000

precommit: ## Run all pre-commit hooks
	pre-commit run --all-files

# ---------- Go HNSW ----------

hnsw-build: ## Build go-hnsw server + bench binaries
	$(MAKE) -C go-hnsw build

hnsw-test: ## Run Go unit tests
	$(MAKE) -C go-hnsw test

hnsw-bench: ## Run SIFT-1M benchmark
	$(MAKE) -C go-hnsw bench

# ---------- Benchmarks ----------

bench-qa: ## Run cross-file QA benchmark (Ragas)
	$(PYTHON) -m benchmarks.cross_file_qa.run_eval

bench-sift: hnsw-bench ## Alias for hnsw-bench

# ---------- Misc ----------

docker: ## Build full dev docker image
	docker build -f docker/Dockerfile -t reposage:dev .

clean: ## Remove caches & build artifacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	$(MAKE) -C go-hnsw clean || true
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
