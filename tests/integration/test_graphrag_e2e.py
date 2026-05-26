"""End-to-end test for Phase 3 GraphRAG.

Index the `tiny_python_repo` fixture with `graphrag=True`, then ask a
module-level question via `RetrievalService` (with the community
retriever wired). Assertions:

* the indexer wrote at least 2 communities (the fixture has obvious
  `auth` and `billing` modules);
* every persisted level-0 community has a non-empty summary;
* the community-route answer carries `graph_context` with ≥ 1
  community and the citations are grounded.

Uses the deterministic `HashEmbedder` + a small custom LLM that always
quotes the first `<retrieved_chunk>` so grounding succeeds.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path

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
from reposage.retrieval.protocols import ChatMessage
from reposage.retrieval.reranker import MockReranker
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.community_store import CommunityStore
from reposage.storage.embeddings_store import EmbeddingsStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


class _SummariserLLM:
    """LLM that returns a fixed-shape JSON for both Map and Reduce."""

    def __init__(self, model: str = "mock-summarizer") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        # The Map prompt's user content includes "Members" while Reduce
        # uses "Parent community" — we don't actually need to switch; a
        # generic JSON works for both.
        return (
            '{"title": "TestModule", "summary": '
            '"Auto-generated summary describing the module purpose, scope, and interactions."}'
        )


@pytest.fixture
def graphrag_indexed(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    db = tmp_path / "index.db"
    pipeline = IndexPipeline(
        repo=repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=True,
        summarizer_llm=_SummariserLLM(),
        community_min_size=2,
    )
    manifest = pipeline.run(force=True)
    assert manifest.failures == []
    assert manifest.n_communities >= 2, f"expected ≥ 2 communities, got {manifest.n_communities}"
    return repo, db


def test_persisted_summaries(graphrag_indexed: tuple[Path, Path]) -> None:
    _, db = graphrag_indexed
    store = CommunityStore(db)
    store.init_schema()
    leaves = [c for c in store.iter_for_repo("tiny") if c.level == 0]
    assert leaves
    assert all(c.summary for c in leaves), "every leaf community should have a summary"
    assert all(c.title for c in leaves), "every leaf community should have a title"
    store.close()


def test_community_embeddings_align_with_summary(
    graphrag_indexed: tuple[Path, Path],
) -> None:
    _, db = graphrag_indexed
    store = CommunityStore(db)
    store.init_schema()
    rows = list(store.iter_embeddings_for_model(model=HashEmbedder().model))
    # At least one community has both a summary and an embedding.
    assert rows
    for cid, title, summary, level, vec in rows:
        assert summary, f"community {cid} has embedding but no summary"
        assert vec.shape == (HashEmbedder().dim,)
        del title, level
    store.close()


def test_community_route_answer_grounded(graphrag_indexed: tuple[Path, Path]) -> None:
    _, db = graphrag_indexed
    embedder = HashEmbedder()

    # Build the dense index from the embeddings already written by the
    # pipeline (mirrors what `_build_cli_dense` does for the local mode).
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    estore = EmbeddingsStore(db)
    try:
        estore.init_schema()
        for ids, mat in estore.iter_vectors(model=embedder.model):
            dense.add(ids, mat)
    finally:
        estore.close()

    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    community = LocalCommunityRetriever.from_sqlite(
        db, model=embedder.model, dim=embedder.dim, repo="tiny"
    )
    if len(community) == 0:
        community = empty_retriever(model=embedder.model, dim=embedder.dim)

    service = RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=MockLLMClient(),
        community=community,
    )

    # Force the community route. The MockLLMClient takes the first
    # <retrieved_chunk> from the user message and cites it back, so
    # grounding should pass as long as we managed to surface any chunks
    # at all for the chosen communities.
    result = asyncio.run(
        service.answer(
            "how do the auth and billing modules interact?",
            repo="tiny",
            route_hint="community",
            top_k=3,
        )
    )
    # The service may degrade to hybrid (degraded_from=community) if no
    # member chunk was discoverable; in either case the answer must be
    # grounded and cite a real file.
    assert result.outcome.route in {"community", "hybrid"}
    assert result.outcome.route == "community" or result.outcome.degraded_from == "community"
    if result.outcome.route == "community":
        assert result.graph_context, "community route must surface graph_context"
        assert any(item.summary for item in result.graph_context)
    assert result.grounded, f"answer not grounded: {result.answer!r}"
    assert result.citations
    for c in result.citations:
        assert c.path
        assert c.start_line >= 1
