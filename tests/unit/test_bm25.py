"""Unit tests for `BM25SparseRetriever`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from reposage.retrieval.bm25 import BM25SparseRetriever, tokenize


def test_tokenize_splits_camelcase_and_dotted_names() -> None:
    # We deliberately keep CamelCase as one lowercased token (cheap path);
    # snake_case splits via the `_` separator.
    assert tokenize("User.login") == ["user", "login"]
    assert tokenize("require_auth(user)") == ["require", "auth", "user"]
    assert tokenize("session timeout 30") == ["session", "timeout"]
    assert tokenize("HTTP/2") == ["http"]


def test_fit_and_search_simple() -> None:
    idx = BM25SparseRetriever()
    idx.fit(
        ids=["c1", "c2", "c3"],
        tokens=[
            ["session", "timeout", "expire"],
            ["session", "open", "user", "login"],
            ["billing", "invoice", "issue"],
        ],
    )
    assert len(idx) == 3


async def test_search_returns_only_matched_docs() -> None:
    idx = BM25SparseRetriever()
    idx.fit(
        ids=["c1", "c2", "c3"],
        tokens=[
            ["session", "timeout"],
            ["billing", "invoice"],
            ["payment", "card"],
        ],
    )
    hits = await idx.search("how is session timeout configured", top_k=10)
    # Only c1 should match — c2/c3 share no tokens with the query.
    assert [h.chunk_id for h in hits] == ["c1"]


async def test_search_empty_query_returns_nothing() -> None:
    idx = BM25SparseRetriever()
    idx.fit(ids=["c1"], tokens=[["foo"]])
    assert await idx.search("???", top_k=5) == []


async def test_load_from_sqlite(tmp_path: Path) -> None:
    """BM25 IDF needs >2 docs to produce non-zero scores for distinguishing terms."""
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chunks(
          chunk_id TEXT PRIMARY KEY, repo TEXT, path TEXT, language TEXT,
          start_line INT, end_line INT, symbol TEXT, parent_symbol TEXT,
          text TEXT, file_sha TEXT, created_at INT
        );
        INSERT INTO chunks VALUES
          ('c1','tiny','a.py','python',1,5,'x',NULL,'session timeout configured 30 seconds','sha',0),
          ('c2','tiny','b.py','python',1,5,'y',NULL,'billing invoice issue','sha',0),
          ('c3','tiny','c.py','python',1,5,'z',NULL,'payment authorize charge card','sha',0),
          ('c4','tiny','d.py','python',1,5,'w',NULL,'logging utilities database connection','sha',0),
          ('c5','other','e.py','python',1,5,'v',NULL,'session login user form','sha',0);
        """
    )
    conn.commit()
    conn.close()

    # Repo-scoped load picks up only the requested repo's docs.
    idx = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    assert len(idx) == 4
    hits = await idx.search("how is session timeout configured", top_k=5)
    assert hits, "expected at least one hit"
    assert hits[0].chunk_id == "c1"


def test_fit_handles_empty_corpus() -> None:
    """Phase 2 indexes a repo before the chunks table is populated; the
    sparse retriever must not explode."""
    idx = BM25SparseRetriever()
    idx.fit(ids=[], tokens=[])
    assert len(idx) == 0


@pytest.mark.asyncio
async def test_search_on_empty_corpus_returns_empty() -> None:
    idx = BM25SparseRetriever()
    idx.fit(ids=[], tokens=[])
    assert await idx.search("anything", top_k=5) == []
