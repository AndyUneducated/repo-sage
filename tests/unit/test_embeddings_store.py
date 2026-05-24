"""Unit tests for `EmbeddingsStore`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest
from reposage.storage.chunk_store import ChunkStore
from reposage.storage.embeddings_store import (
    EmbeddingsStore,
    decode_vector,
    encode_vector,
)


def _seed_chunk(db: Path, chunk_id: str = "c1") -> None:
    """Insert a stub chunk row so FK references resolve."""
    chunks = ChunkStore(db)
    chunks.init_schema()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.execute(
            "INSERT INTO chunks(chunk_id, repo, path, language, start_line, end_line, "
            "                   symbol, parent_symbol, text, file_sha, created_at) "
            "VALUES (?, 'r', 'p.py', 'python', 1, 5, 's', NULL, 't', 'abc', 0)",
            (chunk_id,),
        )
    conn.close()
    chunks.close()


def test_encode_decode_roundtrip() -> None:
    v = np.array([0.1, -0.2, 0.3, 0.4], dtype=np.float32)
    blob = encode_vector(v)
    assert len(blob) == 16
    out = decode_vector(blob, dim=4)
    np.testing.assert_array_equal(out, v)


def test_decode_size_mismatch_raises() -> None:
    blob = b"\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        decode_vector(blob, dim=2)


def test_init_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    s = EmbeddingsStore(db)
    s.init_schema()
    s.init_schema()
    s.close()


def test_upsert_and_get(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_chunk(db, "c1")
    store = EmbeddingsStore(db)
    store.init_schema()
    v = np.array([0.5, -0.5, 1.0, 0.0], dtype=np.float32)
    n = store.upsert([("c1", v)], model="m", dim=4)
    assert n == 1
    got = store.get("c1")
    assert got is not None
    out, model, dim = got
    assert model == "m"
    assert dim == 4
    np.testing.assert_array_equal(out, v)
    store.close()


def test_upsert_dim_mismatch_aborts(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_chunk(db, "c1")
    _seed_chunk(db, "c2")
    store = EmbeddingsStore(db)
    store.init_schema()
    good = np.array([1, 2, 3, 4], dtype=np.float32)
    bad = np.array([1, 2, 3], dtype=np.float32)
    with pytest.raises(ValueError):
        store.upsert([("c1", good), ("c2", bad)], model="m", dim=4)
    # The transaction must not have committed: c1 should NOT exist either.
    assert store.get("c1") is None
    store.close()


def test_iter_vectors_streams_in_batches(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    for i in range(5):
        _seed_chunk(db, f"c{i}")
    store = EmbeddingsStore(db)
    store.init_schema()
    rows = [(f"c{i}", np.array([i, i + 1, i + 2, i + 3], dtype=np.float32)) for i in range(5)]
    store.upsert(rows, model="m", dim=4)
    batches = list(store.iter_vectors(model="m", batch_size=2))
    sizes = [len(ids) for ids, _ in batches]
    assert sizes == [2, 2, 1]
    all_ids = [cid for ids, _ in batches for cid in ids]
    assert all_ids == ["c0", "c1", "c2", "c3", "c4"]
    store.close()


def test_cascade_delete_when_chunk_deleted(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_chunk(db, "c1")
    store = EmbeddingsStore(db)
    store.init_schema()
    store.upsert(
        [("c1", np.array([1, 0, 0, 0], dtype=np.float32))],
        model="m",
        dim=4,
    )
    assert store.count() == 1
    # Drop the chunk via ChunkStore — embeddings must follow.
    chunks = ChunkStore(db)
    chunks.init_schema()
    chunks.delete_by_path("r", "p.py")
    chunks.close()
    assert store.count() == 0
    store.close()


def test_stats_groups_by_model(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    _seed_chunk(db, "c1")
    _seed_chunk(db, "c2")
    store = EmbeddingsStore(db)
    store.init_schema()
    store.upsert([("c1", np.zeros(4, dtype=np.float32))], model="A", dim=4)
    store.upsert([("c2", np.zeros(4, dtype=np.float32))], model="B", dim=4)
    s = store.stats()
    assert s == {"A": (1, 4), "B": (1, 4)}
    store.close()
