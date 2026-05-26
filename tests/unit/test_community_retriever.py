"""Unit tests for `LocalCommunityRetriever`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from reposage.indexer.graphrag.community import Community
from reposage.retrieval.community_retriever import (
    LocalCommunityRetriever,
    empty_retriever,
)
from reposage.storage.community_store import CommunityStore


def test_empty_retriever_returns_no_hits() -> None:
    retr = empty_retriever(model="m", dim=4)
    out = asyncio.run(retr.search([1, 0, 0, 0]))
    assert out == []


def test_search_orders_by_cosine() -> None:
    retr = LocalCommunityRetriever(model="m", dim=4)
    retr.add(
        [
            (1, "Auth", "auth summary", 0, np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)),
            (2, "Billing", "billing summary", 0, np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)),
            (3, "Admin", "admin summary", 1, np.array([0.7, 0.7, 0.0, 0.0], dtype=np.float32)),
        ]
    )
    # Query closest to Auth.
    hits = asyncio.run(retr.search([1, 0, 0, 0], top_k=2))
    assert [h.community_id for h in hits] == [1, 3]
    assert hits[0].title == "Auth"
    assert hits[0].score > hits[1].score


def test_zero_query_returns_empty() -> None:
    retr = LocalCommunityRetriever(model="m", dim=2)
    retr.add([(1, "x", "x summary", 0, np.array([1.0, 0.0], dtype=np.float32))])
    out = asyncio.run(retr.search([0.0, 0.0]))
    assert out == []


def test_dim_mismatch_raises() -> None:
    retr = LocalCommunityRetriever(model="m", dim=4)
    retr.add([(1, "x", "summary text", 0, np.zeros(4, dtype=np.float32))])
    with pytest.raises(ValueError, match="query dim"):
        asyncio.run(retr.search([0, 0, 0]))


def test_from_sqlite_streams_rows(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    store = CommunityStore(db)
    store.init_schema()
    cs = [
        Community(
            id=1,
            members=("a.alpha",),
            level=0,
            parent_id=None,
            content_sha="sha-1",
            title="Auth",
            summary="auth summary",
            summary_model="m",
        )
    ]
    mapping = store.upsert(cs, repo="r")
    store.upsert_embedding(
        mapping[1], np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32), model="m", dim=4
    )
    store.close()

    retr = LocalCommunityRetriever.from_sqlite(db, model="m", dim=4)
    assert len(retr) == 1
    hits = asyncio.run(retr.search([1, 0, 0, 0]))
    assert hits[0].title == "Auth"
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
