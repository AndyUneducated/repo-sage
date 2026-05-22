"""Unit tests for `reposage.storage.chunk_store`."""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.chunker import Chunk, make_chunk_id
from reposage.storage.chunk_store import ChunkStore


def _chunk(repo: str, path: str, start: int, end: int, text: str, **kw: object) -> Chunk:
    chunk_id = make_chunk_id(repo, Path(path), start, end, text)
    return Chunk(
        chunk_id=chunk_id,
        repo=repo,
        path=Path(path),
        language="python",
        text=text,
        start_line=start,
        end_line=end,
        symbol=kw.get("symbol"),  # type: ignore[arg-type]
        parent_symbol=kw.get("parent"),  # type: ignore[arg-type]
    )


def test_init_schema_then_upsert(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = ChunkStore(db)
    store.init_schema()
    n = store.upsert([_chunk("demo", "a.py", 1, 5, "def foo(): pass")], file_sha="abc")
    assert n == 1
    rows = list(store.iter_for_repo("demo"))
    assert len(rows) == 1
    assert rows[0].text == "def foo(): pass"
    store.close()


def test_chunk_id_stable_across_runs(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = ChunkStore(db)
    store.init_schema()
    a = _chunk("demo", "a.py", 1, 5, "x = 1")
    b = _chunk("demo", "a.py", 1, 5, "x = 1")
    assert a.chunk_id == b.chunk_id
    store.upsert([a, b], file_sha="abc")
    rows = list(store.iter_for_repo("demo"))
    assert len(rows) == 1  # second insert was a no-op upsert
    store.close()


def test_delete_by_path(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = ChunkStore(db)
    store.init_schema()
    store.upsert(
        [
            _chunk("demo", "a.py", 1, 5, "def foo(): pass"),
            _chunk("demo", "b.py", 1, 5, "def bar(): pass"),
        ],
        file_sha="abc",
    )
    deleted = store.delete_by_path("demo", "a.py")
    assert deleted == 1
    rows = list(store.iter_for_repo("demo"))
    assert {str(r.path) for r in rows} == {"b.py"}
    store.close()


def test_clear_repo(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    store = ChunkStore(db)
    store.init_schema()
    store.upsert(
        [
            _chunk("demo", "a.py", 1, 5, "x = 1"),
            _chunk("other", "a.py", 1, 5, "x = 2"),
        ],
        file_sha="abc",
    )
    deleted = store.clear_repo("demo")
    assert deleted == 1
    rows = list(store.iter_for_repo("other"))
    assert len(rows) == 1
    store.close()
