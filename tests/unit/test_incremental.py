"""Unit tests for Phase 7 change detection + per-file delete helpers."""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.incremental import affected_files, compute_changeset
from reposage.indexer.symbol_graph import SymbolEdge, SymbolNode
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore


def _module(fqn: str, path: str, repo: str = "demo") -> SymbolNode:
    return SymbolNode(
        fqn=fqn,
        kind="module",
        language="python",
        repo=repo,
        path=path,
        start_line=1,
        end_line=1,
    )


def test_compute_changeset_classifies_every_path() -> None:
    disk = {"a.py": "1", "b.py": "2", "c.py": "3"}  # c changed, a same, b same
    indexed = {"a.py": "1", "b.py": "OLD", "d.py": "9"}  # d deleted, b modified
    cs = compute_changeset(disk, indexed)
    assert cs.added == ("c.py",)
    assert cs.modified == ("b.py",)
    assert cs.deleted == ("d.py",)
    assert cs.unchanged == ("a.py",)
    assert cs.changed == ("c.py", "b.py")
    assert cs.has_changes is True
    assert cs.n_touched == 3


def test_compute_changeset_no_changes() -> None:
    same = {"a.py": "1", "b.py": "2"}
    cs = compute_changeset(same, dict(same))
    assert not cs.has_changes
    assert cs.changed == ()
    assert cs.deleted == ()
    assert set(cs.unchanged) == {"a.py", "b.py"}


def test_affected_files_returns_one_hop_importers(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "x.db")
    store.init_schema()
    # b.py imports a.py; c.py imports nothing relevant.
    store.upsert_nodes([_module("pkg.a", "pkg/a.py"), _module("pkg.b", "pkg/b.py")])
    store.upsert_edges(
        [SymbolEdge(src="pkg.b", dst="pkg.a", kind="import", src_path="pkg/b.py", src_line=1)]
    )
    cs = compute_changeset({"pkg/a.py": "new"}, {"pkg/a.py": "old"})  # a modified
    affected = affected_files(cs, store, repo="demo")
    assert affected == ("pkg/b.py",)
    store.close()


def test_affected_files_empty_when_nothing_touched(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "x.db")
    store.init_schema()
    cs = compute_changeset({"a.py": "1"}, {"a.py": "1"})  # unchanged
    assert affected_files(cs, store, repo="demo") == ()
    store.close()


def test_delete_file_drops_nodes_edges_and_meta(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "x.db")
    store.init_schema()
    store.upsert_nodes([_module("pkg.a", "pkg/a.py"), _module("pkg.b", "pkg/b.py")])
    store.upsert_edges(
        [SymbolEdge(src="pkg.a", dst="pkg.b", kind="call", src_path="pkg/a.py", src_line=3)]
    )
    store.upsert_file_meta(repo="demo", path="pkg/a.py", file_sha="s", mtime=0, parse_status="ok")
    store.upsert_file_meta(repo="demo", path="pkg/b.py", file_sha="s", mtime=0, parse_status="ok")

    store.delete_file("demo", "pkg/a.py")

    assert store.get_node("pkg.a") is None
    assert store.get_node("pkg.b") is not None  # untouched file survives
    assert store.get_file_sha("demo", "pkg/a.py") is None
    assert store.all_files("demo") == {"pkg/b.py": "s"}
    # a.py's outgoing edge is gone.
    conn = store._connect()
    remaining = conn.execute("SELECT COUNT(*) FROM edges WHERE src_path = ?", ("pkg/a.py",))
    assert remaining.fetchone()[0] == 0
    store.close()


def test_get_repo_version_changes_after_reindex(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "x.db")
    store.init_schema()
    assert store.get_repo_version("demo") is None  # never indexed
    store.upsert_repo_meta("demo", head_sha="abc", timestamp=100)
    v1 = store.get_repo_version("demo")
    store.upsert_repo_meta("demo", head_sha="def", timestamp=200)
    v2 = store.get_repo_version("demo")
    assert v1 is not None and v2 is not None and v1 != v2
    assert v2 == "def:200"
    store.close()
