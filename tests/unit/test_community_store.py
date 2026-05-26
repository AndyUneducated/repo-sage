"""Unit tests for `CommunityStore`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from reposage.indexer.graphrag.community import Community
from reposage.storage.community_store import CommunityStore


def _make_community(
    local_id: int,
    members: tuple[str, ...],
    *,
    level: int = 0,
    parent_id: int | None = None,
    child_ids: tuple[int, ...] = (),
    title: str | None = None,
    summary: str | None = None,
) -> Community:
    return Community(
        id=local_id,
        members=members,
        level=level,
        parent_id=parent_id,
        content_sha=f"sha-{local_id}",
        title=title,
        summary=summary,
        summary_model="mock-summarizer" if summary else None,
        child_ids=child_ids,
    )


def _store(tmp_path: Path) -> CommunityStore:
    s = CommunityStore(tmp_path / "c.db")
    s.init_schema()
    return s


def test_roundtrip_flat_partition(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [
        _make_community(1, ("a", "b"), title="Auth", summary="auth module"),
        _make_community(2, ("c", "d"), title="Billing", summary="billing module"),
    ]
    mapping = store.upsert(cs, repo="r")
    assert set(mapping.keys()) == {1, 2}

    fetched = list(store.iter_for_repo("r"))
    titles = sorted(c.title for c in fetched if c.title)
    assert titles == ["Auth", "Billing"]
    members = sorted((c.title, c.members) for c in fetched)
    assert ("Auth", ("a", "b")) in members
    store.close()


def test_parent_child_links(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [
        _make_community(1, ("a", "b"), level=0),
        _make_community(2, ("c", "d"), level=0),
        _make_community(3, ("a", "b", "c", "d"), level=1, child_ids=(1, 2)),
    ]
    cs[0] = replace(cs[0], parent_id=3)
    cs[1] = replace(cs[1], parent_id=3)

    mapping = store.upsert(cs, repo="r")
    parent_db = mapping[3]

    children = [c for c in store.iter_for_repo("r") if c.parent_id == parent_db]
    assert len(children) == 2
    parent = store.get(parent_db)
    assert parent is not None
    assert parent.level == 1
    store.close()


def test_cascade_delete_on_clear(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [_make_community(1, ("a", "b"), title="X", summary="x")]
    mapping = store.upsert(cs, repo="r")
    db_id = mapping[1]
    store.update_summary(db_id, title="X", summary="x", summary_model="mock-summarizer")
    store.upsert_embedding(db_id, np.ones(4, dtype=np.float32), model="m", dim=4)
    store.clear_repo("r")
    assert list(store.iter_for_repo("r")) == []
    # Embeddings table should have cascade-emptied too.
    conn = store._connect()
    n = conn.execute("SELECT COUNT(*) FROM community_embeddings").fetchone()[0]
    assert n == 0
    n = conn.execute("SELECT COUNT(*) FROM community_members").fetchone()[0]
    assert n == 0
    store.close()


def test_find_by_content_sha(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [_make_community(1, ("a",), title="T", summary="S")]
    store.upsert(cs, repo="r")
    found = store.find_by_content_sha("r", "sha-1")
    assert found is not None
    assert found.members == ("a",)
    assert found.title == "T"
    assert store.find_by_content_sha("r", "does-not-exist") is None
    store.close()


def test_find_by_member(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [
        _make_community(1, ("a", "b"), level=0),
        _make_community(2, ("a", "b", "c"), level=1, child_ids=(1,)),
    ]
    cs[0] = replace(cs[0], parent_id=2)
    store.upsert(cs, repo="r")
    hits = store.find_by_member("a")
    assert len(hits) == 2
    assert {h.level for h in hits} == {0, 1}
    assert store.find_by_member("nonexistent") == []
    store.close()


def test_mark_seeds_and_seed_fqns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [_make_community(1, ("a", "b", "c"))]
    mapping = store.upsert(cs, repo="r")
    db_id = mapping[1]
    n = store.mark_seeds(db_id, ["a", "c"])
    assert n == 2
    assert sorted(store.seed_fqns(db_id)) == ["a", "c"]
    store.close()


def test_embedding_requires_summary(tmp_path: Path) -> None:
    """Vectors must not be inserted before `summarized_at` is set —
    otherwise we'd index a placeholder summary as if it were real."""
    store = _store(tmp_path)
    cs = [_make_community(1, ("a",), summary=None)]
    mapping = store.upsert(cs, repo="r")
    db_id = mapping[1]
    with pytest.raises(ValueError, match="summarized_at"):
        store.upsert_embedding(db_id, np.ones(4, dtype=np.float32), model="m", dim=4)
    store.close()


def test_stats_per_level(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cs = [
        _make_community(1, ("a",), level=0),
        _make_community(2, ("b",), level=0),
        _make_community(3, ("a", "b"), level=1, child_ids=(1, 2)),
    ]
    store.upsert(cs, repo="r")
    stats = store.stats(repo="r")
    assert stats == {"level_0": 2, "level_1": 1, "total": 3}
    store.close()
