"""`IndexPipeline` GraphRAG-stage behaviour contracts.

What's pinned here:

1. ``graphrag=False`` is a hard no-op: zero rows land in any community
   table even when the rest of the pipeline runs.
2. ``graphrag=True`` without a ``summarizer_llm`` still writes
   communities (placeholder summaries), so the indexer never refuses to
   build a partition just because the user didn't configure an LLM.
3. Manifest counters (``n_communities``, ``n_community_levels``,
   ``n_community_embeddings``) line up with what's actually persisted —
   we've seen drift between the two in past refactors.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.protocols import ChatMessage

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


class _StubSummariser:
    """LLM stub returning a generic JSON for both Map and Reduce calls."""

    model = "stub-summariser"

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        del messages
        return '{"title": "T", "summary": "S"}'


def _count(db: Path, sql: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    return repo


def test_no_graphrag_leaves_community_tables_empty(fresh_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    manifest = IndexPipeline(
        repo=fresh_repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=False,
    ).run(force=True)

    assert manifest.failures == []
    assert manifest.n_communities == 0
    assert manifest.n_community_levels == 0
    assert manifest.n_community_embeddings == 0

    # Triple-check the persistence side too — manifest fields lie sometimes.
    assert _count(db, "SELECT COUNT(*) FROM communities") == 0
    assert _count(db, "SELECT COUNT(*) FROM community_members") == 0
    assert _count(db, "SELECT COUNT(*) FROM community_embeddings") == 0


def test_graphrag_without_llm_writes_placeholder_summaries(
    fresh_repo: Path, tmp_path: Path
) -> None:
    """No summariser is fine — we still want communities persisted so
    the partition is queryable; just with placeholder summary text.
    Avoids "no LLM → no GraphRAG at all" surprise in fresh environments.
    """
    db = tmp_path / "index.db"
    manifest = IndexPipeline(
        repo=fresh_repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=True,
        summarizer_llm=None,  # explicit
        community_min_size=2,
    ).run(force=True)

    assert manifest.failures == []
    assert manifest.n_communities >= 1
    # No LLM → no embedding fan-out should still happen (summary text
    # exists, even if placeholder, so we DO embed). Just assert the
    # count matches DB.
    assert _count(db, "SELECT COUNT(*) FROM communities") == manifest.n_communities
    assert (
        _count(db, "SELECT COUNT(*) FROM community_embeddings") == manifest.n_community_embeddings
    )


def test_manifest_counts_match_persisted_rows(fresh_repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    manifest = IndexPipeline(
        repo=fresh_repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=True,
        summarizer_llm=_StubSummariser(),
        community_min_size=2,
    ).run(force=True)

    assert manifest.failures == []
    persisted_communities = _count(db, "SELECT COUNT(*) FROM communities")
    persisted_embeddings = _count(db, "SELECT COUNT(*) FROM community_embeddings")
    persisted_members = _count(db, "SELECT COUNT(*) FROM community_members")

    assert manifest.n_communities == persisted_communities
    assert manifest.n_community_embeddings == persisted_embeddings
    # Every community must have at least one member row (no orphans).
    assert persisted_members >= persisted_communities
    # Hierarchy levels reported in the manifest should match what's in
    # the table.
    persisted_levels = _count(db, "SELECT COUNT(DISTINCT level) FROM communities")
    assert manifest.n_community_levels == persisted_levels


def test_reindex_force_replaces_communities(fresh_repo: Path, tmp_path: Path) -> None:
    """`force=True` must drop and re-derive every community — otherwise
    we'd accumulate stale partitions across re-indexes (a real bug we
    were close to introducing in the cascade fix)."""
    db = tmp_path / "index.db"
    pipeline = IndexPipeline(
        repo=fresh_repo,
        sqlite_path=db,
        repo_name="tiny",
        embedder=HashEmbedder(),
        graphrag=True,
        summarizer_llm=_StubSummariser(),
        community_min_size=2,
    )
    pipeline.run(force=True)
    first = _count(db, "SELECT COUNT(*) FROM communities")

    pipeline.run(force=True)
    second = _count(db, "SELECT COUNT(*) FROM communities")

    # The fixture is deterministic; counts should match exactly across re-runs.
    assert first == second
    # And there should be no duplicate community ids hanging around.
    ids = sqlite3.connect(db).execute("SELECT community_id FROM communities").fetchall()
    assert len({i[0] for i in ids}) == len(ids), "duplicate community ids after re-index"
