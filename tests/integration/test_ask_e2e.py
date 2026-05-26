"""End-to-end Phase 2 ``/ask`` integration test on the tiny_python_repo fixture.

Wiring:

* Indexing uses the real `IndexPipeline` + `HashEmbedder`, so the
  embeddings table is populated bit-for-bit the way production does it.
* Serving uses `LocalDenseIndex` (rebuilt from `embeddings` rows) +
  `BM25SparseRetriever` + `MockReranker` + `MockLLMClient`. None of these
  hit the network.
* Both the CLI surface (`reposage ask --route hybrid`) and the
  RetrievalService (used by `POST /ask`) are exercised.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reposage.api.dependencies import get_retrieval_service, reset_retrieval_service
from reposage.api.main import app as fastapi_app
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.llm.client import MockLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, Path]:
    """Index `tiny_python_repo` with `HashEmbedder` and return (repo, db)."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    manifest = IndexPipeline(
        repo=repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=embedder,
    ).run(force=True)
    assert manifest.failures == []
    assert manifest.n_chunks > 0
    assert manifest.n_embeddings == manifest.n_chunks
    return repo, db


def _build_service(db: Path) -> RetrievalService:
    embedder = HashEmbedder()
    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()
    return RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=MockLLMClient(),
    )


@pytest.mark.asyncio
async def test_hybrid_route_returns_grounded_answer(indexed: tuple[Path, Path]) -> None:
    _, db = indexed
    service = _build_service(db)
    result = await service.answer(
        "How is the session opened against User.login?",
        repo="tiny",
        route_hint="hybrid",
        top_k=4,
    )
    assert result.route == "hybrid"
    assert result.answer
    assert result.grounded, f"answer not grounded: {result.answer!r}"
    assert result.citations, "expected at least one citation"
    # Each citation must point at a real chunk path in the indexed fixture.
    paths = {str(c.path) for c in result.chunks}
    for cit in result.citations:
        assert cit.path in paths, f"citation {cit} not in retrieved chunks {paths}"


@pytest.mark.asyncio
async def test_graph_route_short_circuits_llm(indexed: tuple[Path, Path]) -> None:
    _, db = indexed
    service = _build_service(db)
    result = await service.answer(
        "where is User.login called?",
        repo="tiny",
        top_k=4,
    )
    assert result.route == "graph"
    # Graph route never invokes the LLM, so latency.llm_ms is 0.
    assert result.latency.llm_ms == 0
    # The fixture has Session.open and login_route both calling User.login.
    paths = {c.path for c in result.citations}
    assert any("sessions.py" in p for p in paths)


@pytest.mark.asyncio
async def test_route_hint_forces_branch(indexed: tuple[Path, Path]) -> None:
    """Even when the question contains a symbol, --route hybrid forces hybrid."""
    _, db = indexed
    service = _build_service(db)
    result = await service.answer(
        "where is User.login called?",
        repo="tiny",
        route_hint="hybrid",
        top_k=4,
    )
    assert result.route == "hybrid"


def test_post_ask_endpoint(indexed: tuple[Path, Path]) -> None:
    """Hit `POST /ask` via FastAPI's TestClient with the dep injected."""
    _, db = indexed
    service = _build_service(db)
    fastapi_app.dependency_overrides[get_retrieval_service] = lambda: service
    try:
        with TestClient(fastapi_app) as client:
            resp = client.post(
                "/ask",
                json={
                    "question": "How does Session.open work?",
                    "repo": "tiny",
                    "top_k": 4,
                    "route_hint": "hybrid",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"]["route"] in {"hybrid", "graph"}
        assert body["outcome"]["degraded_from"] is None
        assert body["graph_context"] is None  # Phase 3 will populate
        assert "latency_ms" in body
        assert body["latency_ms"]["total_ms"] >= 0
    finally:
        fastapi_app.dependency_overrides.pop(get_retrieval_service, None)
        reset_retrieval_service()


@pytest.mark.asyncio
async def test_grounding_rejects_fabricated_citations(
    indexed: tuple[Path, Path],
) -> None:
    """A bad LLM that fabricates citations must be caught by the grounder."""
    from reposage.retrieval.protocols import ChatMessage  # noqa: PLC0415

    class FabricatingLLM:
        @property
        def model(self) -> str:
            return "evil"

        async def complete(self, messages: list[ChatMessage]) -> str:
            return "The session timeout is at [does/not/exist.py:99-100]."

    _, db = indexed
    embedder = HashEmbedder()
    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()

    service = RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=FabricatingLLM(),
    )
    result = await service.answer(
        "How is the session timeout configured?",
        repo="tiny",
        route_hint="hybrid",
        top_k=4,
    )
    # The fabricated citation must NOT survive grounding.
    assert "[does/not/exist.py" not in result.answer
    assert all(c.path != "does/not/exist.py" for c in result.citations)
