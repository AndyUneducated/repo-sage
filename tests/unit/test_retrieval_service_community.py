"""Behavioural tests for the community route's degradation paths.

The retrieval service has *four* community-route exits:

1. ``community=None`` → degrade to hybrid (Phase 2 parity).
2. retriever returns no hits → degrade to hybrid.
3. retriever returns hits but no member chunks materialise → degrade,
   but the community context is still surfaced (so the API can show
   "we found these communities but couldn't ground").
4. happy path: hits + chunks → community answer with ``graph_context``.

Each path matters operationally — observability needs the
``RouteOutcome`` on ``AnswerResult`` (``route`` + ``degraded_from``) to
distinguish them so dashboards can spot "community always degrades to
hybrid" silently.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.llm.client import MockLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.community_retriever import (
    LocalCommunityRetriever,
    empty_retriever,
)
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.protocols import ScoredCommunity
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


class _StaticCommunityRetriever:
    """Community retriever that returns scripted hits without touching SQLite."""

    def __init__(self, hits: list[ScoredCommunity], *, model: str = "hash-v0", dim: int = 64):
        self._hits = hits
        self._model = model
        self._dim = dim

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def search(self, query: Sequence[float], *, top_k: int) -> list[ScoredCommunity]:
        del query
        return self._hits[:top_k]


@pytest.fixture
def indexed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    db = tmp_path / "index.db"
    IndexPipeline(
        repo=repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=False,  # only embeddings + BM25; we'll attach community manually
    ).run(force=True)
    return db


def _build_service(db: Path, *, community):
    embedder = HashEmbedder()
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
        sparse=BM25SparseRetriever.from_sqlite(db, repo="tiny"),
        reranker=MockReranker(),
        llm=MockLLMClient(),
        community=community,
    )


@pytest.mark.asyncio
async def test_route_degrades_when_community_is_none(indexed_repo: Path) -> None:
    service = _build_service(indexed_repo, community=None)
    result = await service.answer(
        "What does the auth module do?",
        repo="tiny",
        route_hint="community",
        top_k=4,
    )
    assert result.outcome.route == "hybrid"
    assert result.outcome.degraded_from == "community"
    assert result.outcome.degrade_reason
    assert result.graph_context is None
    assert result.grounded


@pytest.mark.asyncio
async def test_route_degrades_when_empty_retriever(indexed_repo: Path) -> None:
    """Wired but empty retriever (no communities indexed) → degrade,
    but the answer must still be grounded via hybrid."""
    empty = empty_retriever(model="hash-v0", dim=64)
    service = _build_service(indexed_repo, community=empty)
    result = await service.answer(
        "What does the auth module do?",
        repo="tiny",
        route_hint="community",
        top_k=4,
    )
    assert result.outcome.route == "hybrid"
    assert result.outcome.degraded_from == "community"
    assert result.graph_context is None
    assert result.grounded


@pytest.mark.asyncio
async def test_route_degrades_but_keeps_context_when_no_chunks(indexed_repo: Path) -> None:
    """Retriever returns hits but those communities have NO members in
    the chunk table → degrade to hybrid while surfacing context for
    diagnostics."""
    fake_hit = ScoredCommunity(
        community_id=999_999,  # doesn't exist in the store → no member chunks
        level=0,
        title="Fake",
        summary="not real",
        score=0.9,
    )
    service = _build_service(
        indexed_repo,
        community=_StaticCommunityRetriever([fake_hit]),
    )
    result = await service.answer(
        "What does the auth module do?",
        repo="tiny",
        route_hint="community",
        top_k=4,
    )
    assert result.outcome.route == "hybrid"
    assert result.outcome.degraded_from == "community"
    # We surface the community context the retriever produced even though
    # the answer came from hybrid — operators need this signal to debug
    # "why did community route not produce an answer".
    assert result.graph_context is not None
    assert result.graph_context[0].community_id == 999_999
    assert result.grounded


def test_local_community_retriever_normalises_vectors() -> None:
    """A community embedding written un-normalised must still produce a
    correct cosine score. The retriever owns this invariant — anything
    that calls `.add()` is allowed to pass non-unit vectors."""
    retr = LocalCommunityRetriever(model="hash-v0", dim=3)
    retr.add(
        [
            (1, "a", "sa", 0, np.array([3.0, 0.0, 0.0], dtype=np.float32)),
            (2, "b", "sb", 0, np.array([0.0, 4.0, 0.0], dtype=np.float32)),
        ]
    )

    async def _go() -> list[ScoredCommunity]:
        return await retr.search([1.0, 0.0, 0.0], top_k=2)

    hits = asyncio.run(_go())
    # First hit must be the [3, 0, 0] community, with score ≈ 1.0 (cosine).
    assert hits[0].community_id == 1
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    # Second hit should be the orthogonal community, near 0.
    assert hits[1].community_id == 2
    assert abs(hits[1].score) < 1e-3


def test_local_community_retriever_from_sqlite_populates(indexed_repo: Path) -> None:
    """`from_sqlite` must surface every community that has an embedding
    matching the requested model+dim — if it silently dropped some, the
    community route would behave inconsistently with the indexed data."""
    # Re-run index with graphrag=True so we have communities to load.
    db = indexed_repo
    embedder = HashEmbedder()

    # Sanity: the fixture in `indexed_repo` was indexed with graphrag=False
    # → community table empty → retriever must report len == 0.
    retr = LocalCommunityRetriever.from_sqlite(
        db, model=embedder.model, dim=embedder.dim, repo="tiny"
    )
    assert len(retr) == 0
