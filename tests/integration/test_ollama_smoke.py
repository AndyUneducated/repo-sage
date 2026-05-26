"""End-to-end smoke test against a real local Ollama daemon.

Marked `requires_ollama` so `make test` skips it. Run via `make
test-ollama` after starting Ollama and pulling the configured model
(default ``ollama_chat/qwen2.5-coder:7b``).

What this asserts is intentionally weak: real LLMs are non-deterministic
so we cannot pin the exact answer text. Instead we verify:

* the request actually flowed through LiteLLM to Ollama;
* the response came back non-empty;
* the citation grounder either kept a valid citation OR stripped a bad
  one without raising;
* total latency is bounded, so a wedged daemon fails the suite instead
  of hanging CI for hours.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from reposage.config import get_settings
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.llm.client import LiteLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

pytestmark = pytest.mark.requires_ollama

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


def _ollama_tags(api_base: str) -> set[str] | None:
    """Return the set of pulled model names, or None if Ollama is unreachable."""
    try:
        with urlopen(api_base.rstrip("/") + "/api/tags", timeout=2) as r:
            if r.status >= 400:
                return None
            payload = json.loads(r.read().decode("utf-8") or "{}")
    except (URLError, TimeoutError, OSError):
        return None
    return {str(m.get("name", "")) for m in payload.get("models", [])}


def _strip_ollama_prefix(model: str) -> str:
    for p in ("ollama/", "ollama_chat/"):
        if model.startswith(p):
            return model[len(p) :]
    return model


@pytest.fixture
def ollama_settings() -> tuple[str, str]:
    settings = get_settings()
    if not settings.llm_model.startswith(("ollama/", "ollama_chat/")):
        pytest.skip(
            f"LLM_MODEL is {settings.llm_model!r}, not an Ollama model; "
            "set LLM_MODEL=ollama_chat/<name> to run this smoke test."
        )
    available = _ollama_tags(settings.ollama_api_base)
    if available is None:
        pytest.skip(
            f"Ollama not reachable at {settings.ollama_api_base}; "
            "start it with `ollama serve` to run this test."
        )
    bare = _strip_ollama_prefix(settings.llm_model)
    candidates = {bare} if ":" in bare else {bare, f"{bare}:latest"}
    if not (candidates & available):
        pretty = ", ".join(sorted(available)) or "(none)"
        pytest.skip(
            f"Ollama at {settings.ollama_api_base} does not have {bare!r}. "
            f"Available: {pretty}. Run `ollama pull {bare}` to enable."
        )
    return settings.llm_model, settings.ollama_api_base


@pytest.mark.asyncio
async def test_real_ollama_answers_with_grounded_or_stripped_citation(
    ollama_settings: tuple[str, str], tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"

    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)

    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()

    # LiteLLMClient picks model + api_base from settings.
    llm = LiteLLMClient()
    service = RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=llm,
    )
    try:
        result = await service.answer(
            "How does Session.open work?",
            repo="tiny",
            route_hint="hybrid",
            top_k=4,
        )
    finally:
        # Drain LiteLLM's logging queue before the loop dies — see
        # `LiteLLMClient.aclose` for context on the orphaned-coroutine warning.
        await llm.aclose()
    assert result.answer.strip(), "Ollama returned an empty answer"
    # Bound the round-trip so a wedged daemon doesn't hang CI for hours.
    # qwen2.5-coder:7b on CPU sits around 2-15s per call; we leave a wide
    # margin since the smoke gate is correctness, not latency.
    assert result.latency.total_ms < 120_000, (
        f"Ollama round-trip {result.latency.total_ms} ms exceeds smoke budget"
    )
    # Either grounded with citations, OR ungrounded with citations stripped.
    # Both are valid outcomes — the LLM is allowed to refuse on weak context.
    if result.grounded:
        assert all(
            any(str(c.path) == cit.path for c in result.chunks) for cit in result.citations
        ), "grounded answer must only cite retrieved chunk paths"
    else:
        # Stripped citations leave the marker; no fabricated path survives.
        for cit in result.citations:
            assert any(str(c.path) == cit.path for c in result.chunks)
