"""Unit tests for `reposage.storage.sqlite_graph`."""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.symbol_graph import SymbolEdge, SymbolNode
from reposage.storage.sqlite_graph import SCHEMA_VERSION, SQLiteSymbolGraphStore


def _node(fqn: str, **kw: object) -> SymbolNode:
    base = dict(
        kind="function",
        language="python",
        repo="demo",
        path="pkg/mod.py",
        start_line=1,
        end_line=2,
    )
    base.update(kw)
    return SymbolNode(fqn=fqn, **base)  # type: ignore[arg-type]


def _edge(src: str, dst: str, kind: str = "call", line: int = 10) -> SymbolEdge:
    return SymbolEdge(src=src, dst=dst, kind=kind, src_path="pkg/mod.py", src_line=line)  # type: ignore[arg-type]


def test_init_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.init_schema()  # second call must not fail
    conn = store._connect()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    store.close()


def test_upsert_and_callers_of(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.upsert_nodes([_node("pkg.mod.User"), _node("pkg.mod.api")])
    store.upsert_edges([_edge("pkg.mod.api", "pkg.mod.User", line=5)])
    callers = store.callers_of("pkg.mod.User")
    assert len(callers) == 1
    assert callers[0].src == "pkg.mod.api"
    assert callers[0].src_line == 5
    store.close()


def test_duplicate_edge_increments_weight(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.upsert_nodes([_node("pkg.mod.A"), _node("pkg.mod.B")])
    e = _edge("pkg.mod.A", "pkg.mod.B", line=5)
    store.upsert_edges([e])
    store.upsert_edges([e])  # same line — should bump weight, not duplicate
    conn = store._connect()
    weight = conn.execute(
        "SELECT weight FROM edges WHERE src=? AND dst=? AND src_line=?",
        ("pkg.mod.A", "pkg.mod.B", 5),
    ).fetchone()[0]
    assert weight == 2
    store.close()


def test_find_nodes_by_suffix(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.upsert_nodes(
        [
            _node("pkg.auth.User"),
            _node("pkg.auth.User.login"),
            _node("pkg.api.User.login"),  # duplicate symbol in different module
            _node("pkg.helpers.helper"),
        ]
    )
    matches = store.find_nodes_by_suffix("User.login")
    fqns = sorted(m.fqn for m in matches)
    assert fqns == ["pkg.api.User.login", "pkg.auth.User.login"]
    # Exact-match case
    exact = store.find_nodes_by_suffix("pkg.helpers.helper")
    assert [m.fqn for m in exact] == ["pkg.helpers.helper"]
    store.close()


def test_clear_repo_drops_only_owned_rows(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.upsert_nodes([_node("a", repo="demo"), _node("b", repo="other", path="other/file.py")])
    store.upsert_file_meta(
        repo="demo", path="pkg/mod.py", file_sha="aa", mtime=0, parse_status="ok"
    )
    store.upsert_file_meta(
        repo="other", path="other/file.py", file_sha="bb", mtime=0, parse_status="ok"
    )
    store.upsert_edges([_edge("a", "b")])
    store.clear_repo("demo")
    assert store.get_node("a") is None
    assert store.get_node("b") is not None
    assert store.parse_status_counts("demo") == {}
    assert store.parse_status_counts("other") == {"ok": 1}
    store.close()


def test_file_meta_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    store.upsert_file_meta(
        repo="demo", path="pkg/x.py", file_sha="abc", mtime=42, parse_status="ok"
    )
    assert store.get_file_sha("demo", "pkg/x.py") == "abc"
    assert store.parse_status_counts("demo") == {"ok": 1}
    # Same path, different sha — should overwrite, not duplicate.
    store.upsert_file_meta(
        repo="demo", path="pkg/x.py", file_sha="def", mtime=43, parse_status="ok"
    )
    assert store.get_file_sha("demo", "pkg/x.py") == "def"
    assert store.parse_status_counts("demo") == {"ok": 1}
    store.close()
