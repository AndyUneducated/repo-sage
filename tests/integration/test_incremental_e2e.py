"""Phase 7 incremental (non-force) re-index correctness on real pipeline runs.

These pin the three bugs the incremental path used to have:

1. Edge ``weight`` inflated on every re-index (ON CONFLICT weight+1 with no
   pre-clear of the changed file's edges).
2. Files deleted on disk were never purged from the index.
3. A file edited down to zero chunks left its old chunks (and cascaded
   embeddings) behind.
"""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.storage.chunk_store import ChunkStore
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

MOD_PY = """\
def target():
    return 1


def caller():
    return target()
"""


def _edge_weight(db: Path, src: str, dst: str) -> int | None:
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    try:
        conn = store._connect()
        row = conn.execute(
            "SELECT weight FROM edges WHERE src = ? AND dst = ? AND kind = 'call'",
            (src, dst),
        ).fetchone()
        return None if row is None else int(row[0])
    finally:
        store.close()


def test_reindex_does_not_inflate_edge_weight(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(MOD_PY)
    db = tmp_path / "index.db"

    IndexPipeline(repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()).run(force=True)
    w0 = _edge_weight(db, "mod.caller", "mod.target")
    assert w0 == 1, f"baseline call edge weight should be 1, got {w0}"

    # Two *distinct* trailing-comment edits (call-site line unchanged → same
    # edge key). Each new sha forces a re-resolve; without the pre-clear fix
    # the weight would climb 1 → 2 → 3.
    for marker in ("# v1", "# v2"):
        (repo / "mod.py").write_text(f"{MOD_PY}\n{marker}\n")
        manifest = IndexPipeline(
            repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()
        ).run(force=False)
        assert manifest.n_python_files == 1  # the changed file was re-resolved

    w1 = _edge_weight(db, "mod.caller", "mod.target")
    assert w1 == 1, f"edge weight must not inflate across re-indexes, got {w1}"


def test_deleted_file_is_purged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.py").write_text("def kept():\n    return 1\n")
    (repo / "gone.py").write_text("def doomed():\n    return 2\n")
    db = tmp_path / "index.db"

    IndexPipeline(repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()).run(force=True)
    assert _node_exists(db, "gone.doomed")

    (repo / "gone.py").unlink()
    manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()).run(
        force=False
    )

    assert manifest.n_deleted_files == 1
    assert not _node_exists(db, "gone.doomed")
    assert _node_exists(db, "keep.kept")

    chunks = ChunkStore(db)
    chunks.init_schema()
    paths = {str(c.path) for c in chunks.iter_for_repo("r")}
    chunks.close()
    assert "gone.py" not in paths
    assert "keep.py" in paths


def test_emptied_file_purges_stale_chunks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def something():\n    return 1\n")
    db = tmp_path / "index.db"

    IndexPipeline(repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()).run(force=True)
    assert _chunk_count(db, "r") > 0

    # Edit to whitespace-only: chunker yields nothing, but the file still
    # exists (so it's not a "delete"). Old chunks must not linger.
    (repo / "mod.py").write_text("\n\n")
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="r", embedder=HashEmbedder()).run(
        force=False
    )
    assert _chunk_count(db, "r") == 0


def _graph_snapshot(db: Path) -> tuple[frozenset[tuple], frozenset[tuple]]:
    """Full (nodes, edges) snapshot for equivalence comparison."""
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    try:
        conn = store._connect()
        nodes = frozenset(
            conn.execute("SELECT fqn, kind, path, start_line, end_line FROM nodes").fetchall()
        )
        edges = frozenset(
            conn.execute("SELECT src, dst, kind, src_path, src_line, weight FROM edges").fetchall()
        )
        return nodes, edges
    finally:
        store.close()


def test_incremental_matches_full_rebuild(tmp_path: Path) -> None:
    """Phase 7 exit criterion: an incremental re-index of a symbol-preserving
    edit produces the *same* symbol graph as a full rebuild of the final tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(MOD_PY)
    (repo / "b.py").write_text("def helper():\n    return 1\n\n\ndef run():\n    return helper()\n")

    db_inc = tmp_path / "inc.db"
    IndexPipeline(repo=repo, sqlite_path=db_inc, repo_name="r", embedder=HashEmbedder()).run(
        force=True
    )
    # Symbol-preserving edit (trailing comment: no symbol/line changes).
    (repo / "a.py").write_text(MOD_PY + "\n# incremental edit\n")
    IndexPipeline(repo=repo, sqlite_path=db_inc, repo_name="r", embedder=HashEmbedder()).run(
        force=False
    )

    # Full rebuild of the *final* tree into a fresh DB.
    db_full = tmp_path / "full.db"
    IndexPipeline(repo=repo, sqlite_path=db_full, repo_name="r", embedder=HashEmbedder()).run(
        force=True
    )

    assert _graph_snapshot(db_inc) == _graph_snapshot(db_full)


def _node_exists(db: Path, fqn: str) -> bool:
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    try:
        return store.get_node(fqn) is not None
    finally:
        store.close()


def _chunk_count(db: Path, repo: str) -> int:
    chunks = ChunkStore(db)
    chunks.init_schema()
    try:
        return len(list(chunks.iter_for_repo(repo)))
    finally:
        chunks.close()
