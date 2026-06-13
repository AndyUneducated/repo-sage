.PHONY: help install install-dev fmt lint typecheck test test-grpc test-ollama cov dev hnsw-build hnsw-run hnsw-test hnsw-bench bench-qa bench-rag bench-sift bench-sift-synthetic bench-graph proto-gen clean precommit docker

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

test: ## Run unit + integration tests (skips gRPC + Ollama marks; bench-rag uses mock)
	REPOSAGE_PROFILE=mock pytest -q -m "not requires_go_hnsw and not requires_ollama"

test-grpc: hnsw-build ## Run gRPC integration tests against a live hnsw-server
	pytest -q -m requires_go_hnsw

test-ollama: ## Run real-LLM smoke tests against a local Ollama daemon
	pytest -q -m requires_ollama

cov: ## Run tests with coverage
	pytest --cov=reposage --cov-report=term-missing --cov-report=xml

dev: ## Run FastAPI in reload mode
	uvicorn reposage.api.main:app --reload --host 0.0.0.0 --port 8000

precommit: ## Run all pre-commit hooks
	pre-commit run --all-files

# ---------- Go HNSW ----------

hnsw-build: ## Build go-hnsw server + bench binaries
	$(MAKE) -C go-hnsw build

hnsw-run: hnsw-build ## Run hnsw-server against the configured SQLite DB
	./go-hnsw/bin/hnsw-server \
		-addr 127.0.0.1:50051 \
		-db ./data/reposage.db \
		-model BAAI/bge-en-v1.5 \
		-dim 768 -m 16 -ef-construction 200 -ef-search 64 \
		-snapshot ./data/hnsw/index.hnsw -snapshot-on-exit

hnsw-test: ## Run Go unit tests
	$(MAKE) -C go-hnsw test

hnsw-bench: ## Run synthetic smoke benchmark (no dataset download)
	$(MAKE) -C go-hnsw bench

# ---------- Protobuf ----------

proto-gen: ## Regenerate Python and Go gRPC stubs from proto/*.proto
	mkdir -p reposage/proto go-hnsw/hnswpb
	python -m grpc_tools.protoc -Iproto \
		--python_out=reposage/proto \
		--grpc_python_out=reposage/proto \
		--pyi_out=reposage/proto proto/hnsw.proto
	# protoc emits flat `import hnsw_pb2`; rewrite it as a relative import
	# so the generated module works under `reposage.proto.*`.
	sed -i.bak 's|^import hnsw_pb2 as|from . import hnsw_pb2 as|' \
		reposage/proto/hnsw_pb2_grpc.py && rm -f reposage/proto/hnsw_pb2_grpc.py.bak
	protoc -Iproto \
		--go_out=go-hnsw/hnswpb --go_opt=paths=source_relative \
		--go-grpc_out=go-hnsw/hnswpb --go-grpc_opt=paths=source_relative \
		proto/hnsw.proto

# ---------- Benchmarks ----------

bench-qa: ## Run Phase 3 cross-file QA benchmark (community vs hybrid; set REPOSAGE_PROFILE=mock to bypass Ollama)
	$(PYTHON) -m benchmarks.cross_file_qa.run_eval $(if $(VERBOSE),-v,)

bench-qa-community: bench-qa ## Alias kept for the Phase 3 plan.

bench-graph: ## Run Phase 1 graph-query benchmark (precision >= 0.90)
	$(PYTHON) -m benchmarks.graph_queries.run_eval $(if $(LARGE),--large,)

bench-rag: ## Run Phase 2 hybrid RAG benchmark against Ollama (set REPOSAGE_PROFILE=mock to bypass)
	$(PYTHON) -m benchmarks.rag.run_eval $(if $(LARGE),--large,)

bench-sift: hnsw-build ## Full SIFT-1M sweep + Faiss baseline + Pareto plot (needs dataset + .[bench])
	$(PYTHON) benchmarks/sift1m/run_sweep.py --dataset-dir benchmarks/sift1m/data/sift \
		--snapshot benchmarks/sift1m/data/index.hnsw --faiss --write-docs

bench-sift-synthetic: hnsw-build ## CI-friendly synthetic sweep (no dataset download)
	$(PYTHON) benchmarks/sift1m/run_sweep.py --synthetic 20000 --M 8 16 32 \
		--efC 100 200 --ef 16 32 64 128 256 --snapshot /tmp/sift-synth.hnsw

# ---------- Misc ----------

docker: ## Build full dev docker image
	docker build -f docker/Dockerfile -t reposage:dev .

clean: ## Remove caches & build artifacts
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	$(MAKE) -C go-hnsw clean || true
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
