"""Shared bench helpers: Ollama health-check, fixture indexing, async wiring.

What lives here:

* `OllamaUnavailableError` + `check_ollama_available`: shared health-check
  surface; both bench runners call it before any LiteLLM round-trip.
* `load_questions(path)`: one JSONL parser for the per-runner fixtures.
* `index_fixture_and_build_service(...)`: indexes the fixture with
  `HashEmbedder` and builds a wired `RetrievalService` ready for both
  the Phase 2 RAG and Phase 3 cross-file QA benches.
* `run_with_aclose(coro_factory, llm)`: drains LiteLLM telemetry before
  `asyncio.run` closes the loop, eliminating the "coroutine was never
  awaited" warning at shutdown.
* `add_large_arg` / `resolve_large_repo` / `add_verbose_arg`: argparse
  helpers shared by both runners.

What does *not* live here (and stays per-runner):

* Each runner's metric dataclass + `summarise()` (Phase 2 RAG and Phase
  3 QA report different things).
* Each runner's `main()` entry point + threshold assertions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.llm.client import MockLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.community_retriever import (
    LocalCommunityRetriever,
    empty_retriever,
)
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.protocols import LLMClient
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tiny_python_repo"

_OLLAMA_MODEL_PREFIX = ("ollama/", "ollama_chat/")


class OllamaUnavailableError(RuntimeError):
    """Ollama health-check failed; ships a clear remediation hint."""


def strip_ollama_prefix(model: str) -> str:
    """Strip the LiteLLM `ollama_chat/` / `ollama/` namespace from a model name."""
    for p in _OLLAMA_MODEL_PREFIX:
        if model.startswith(p):
            return model[len(p) :]
    return model


def is_ollama_model(model: str) -> bool:
    return model.startswith(_OLLAMA_MODEL_PREFIX)


def check_ollama_available(model: str, api_base: str, timeout: float = 2.0) -> None:
    """Ping Ollama and verify the tag is pulled.

    Raises `OllamaUnavailableError` with a clear `ollama pull <name>` hint
    when (1) the daemon is unreachable, or (2) the daemon is up but the
    requested tag is not pulled. Caught by the runner's `main()` so a
    misconfigured local box fails fast instead of producing degraded
    recall/latency numbers mid-bench.
    """
    bare = strip_ollama_prefix(model)
    url = api_base.rstrip("/") + "/api/tags"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                raise OllamaUnavailableError(f"GET {url} -> HTTP {resp.status}")
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except (URLError, TimeoutError, OSError) as exc:
        raise OllamaUnavailableError(
            f"Cannot reach Ollama at {api_base}: {exc}.\n"
            f"Either start Ollama (`ollama serve` and `ollama pull "
            f"{bare}`) or set REPOSAGE_PROFILE=mock to fall back to the "
            "offline mock pipeline."
        ) from exc

    available = {str(m.get("name", "")) for m in payload.get("models", [])}
    # Ollama tags include the explicit `:tag` suffix. Treat `name` and
    # `name:latest` as equivalent so `llama3` matches `llama3:latest`.
    candidates = {bare} if ":" in bare else {bare, f"{bare}:latest"}
    if not (candidates & available):
        pretty = ", ".join(sorted(available)) or "(none)"
        raise OllamaUnavailableError(
            f"Ollama is reachable at {api_base} but model {bare!r} is not "
            f"pulled. Available: {pretty}.\n"
            f"Run `ollama pull {bare}` or set REPOSAGE_PROFILE=mock to use "
            "the offline mock pipeline."
        )


def load_questions(path: Path) -> list[dict[str, object]]:
    """Parse a JSONL fixture file (one question per non-empty line)."""
    out: list[dict[str, object]] = []
    with path.open() as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def index_fixture_and_build_service(
    *,
    fixture: Path,
    db: Path,
    repo_name: str,
    llm: LLMClient,
    graphrag: bool = False,
    community_top_k: int = 5,
    community_chunks_per_hit: int = 4,
) -> RetrievalService:
    """Index `fixture` into `db` and return a wired `RetrievalService`.

    With `graphrag=False` (Phase 2 RAG bench) the indexer skips Leiden /
    summary writes and the resulting service has no community retriever.
    With `graphrag=True` (Phase 3 cross-file QA bench) the indexer runs
    Leiden under a `MockLLMClient` summariser so the run stays offline,
    and the service is wired with a `LocalCommunityRetriever` so the
    community route works end-to-end.
    """
    embedder = HashEmbedder()
    pipeline = IndexPipeline(
        repo=fixture,
        sqlite_path=db,
        repo_name=repo_name,
        embedder=embedder,
        graphrag=graphrag,
        summarizer_llm=MockLLMClient(model="bench-summarizer") if graphrag else None,
        community_min_size=2,
    )
    pipeline.run(force=True)

    sparse = BM25SparseRetriever.from_sqlite(db, repo=repo_name)
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    try:
        es.init_schema()
        for ids, mat in es.iter_vectors(model=embedder.model):
            dense.add(ids, mat)
    finally:
        es.close()

    community = None
    if graphrag:
        local = LocalCommunityRetriever.from_sqlite(
            db, model=embedder.model, dim=embedder.dim, repo=repo_name
        )
        community = (
            empty_retriever(model=embedder.model, dim=embedder.dim) if len(local) == 0 else local
        )

    return RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=llm,
        community=community,
        community_top_k=community_top_k,
        community_chunks_per_hit=community_chunks_per_hit,
    )


def run_with_aclose[T](coro_factory: Callable[[], Awaitable[T]], llm: LLMClient) -> T:
    """Run `coro_factory()` under `asyncio.run`, draining `llm.aclose()` first.

    LiteLLM's global LoggingWorker binds to the *first* event loop it sees;
    when `asyncio.run` closes that loop the pending telemetry coroutines
    are orphaned and trigger ``RuntimeWarning: coroutine ... was never
    awaited``. Calling `llm.aclose()` from inside the loop drains the
    worker before teardown; in-process mocks treat it as a no-op.
    """

    async def _run() -> T:
        try:
            return await coro_factory()
        finally:
            await llm.aclose()

    return asyncio.run(_run())


def add_large_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--large",
        action="store_true",
        help="Run against $REPOSAGE_LARGE_REPO instead of the tiny fixture.",
    )


def resolve_large_repo() -> Path | None:
    """Resolve the `--large` repo or return None.

    Prints a remediation hint to stderr when the env var is unset OR the
    path does not exist; callers should treat `None` as "skip the large
    repo path".
    """
    repo_str = os.environ.get("REPOSAGE_LARGE_REPO", str(REPO_ROOT / ".bench" / "large_repo"))
    repo = Path(repo_str)
    if not repo.exists():
        print(
            f"large repo not found at {repo}; skipping perf check.\n"
            "Set REPOSAGE_LARGE_REPO to a >=50 kLOC python checkout to enable.",
            file=sys.stderr,
        )
        return None
    return repo


def add_verbose_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--verbose", "-v", action="store_true")
