"""SQLite-backed store for chunk embeddings (Phase 2).

Vectors are stored as little-endian float32 BLOBs keyed by `chunk_id`. The
table sits in the same `data/reposage.db` as `chunks` and the symbol graph,
so a single file holds everything `reposage ask` needs.

Why SQLite BLOB instead of a sidecar `.npy` (DD-011):
    * Atomic writes (transactions); crash-mid-index never produces half-state.
    * One file to back up / move across machines / deduplicate by file_sha.
    * Multi-model story: the `model` column lets two embedding sets coexist
      so Phase 7 can A/B a new encoder before flipping the default.
    * Phase 5 mmap snapshot is a one-shot export, not a coupled file format.

Cold-start read at the embedding scale we serve in Phase 2 (~10k chunks for
50 kLOC) is < 100 ms even on rotational disk, so the SELECT-then-Add startup
of the HNSW server is cheap. See `docs/plans/phase-2-retrieval.md` for the
benchmarking note.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id   TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vector     BLOB NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_model ON embeddings(model);
"""


def encode_vector(vec: np.ndarray) -> bytes:
    """Pack a 1-D float32 array as a little-endian byte BLOB."""
    if vec.ndim != 1:
        raise ValueError(f"expected 1-D vector, got shape {vec.shape}")
    return vec.astype("<f4", copy=False).tobytes()


def decode_vector(blob: bytes, dim: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype="<f4")
    if arr.size != dim:
        raise ValueError(f"vector size {arr.size} does not match dim={dim}")
    # `np.frombuffer` returns a read-only view into the bytes; copy so
    # downstream code can mutate (e.g. L2-normalise) without surprise.
    return np.array(arr, copy=True)


class EmbeddingsStore:
    """CRUD for the `embeddings` table."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        # Required so the chunks(chunk_id) → embeddings ON DELETE CASCADE
        # actually fires on this connection's writes.
        conn.execute("PRAGMA foreign_keys = ON")
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_schema(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    def upsert(
        self,
        items: Iterable[tuple[str, np.ndarray]],
        *,
        model: str,
        dim: int,
        timestamp: int | None = None,
    ) -> int:
        """Upsert ``(chunk_id, vector)`` pairs for a single model.

        Vectors must be 1-D float32-compatible arrays of length ``dim``.
        Items whose dim does not match raise ``ValueError`` *before* any row
        is written, so partial commits never happen.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        rows: list[tuple[str, str, int, bytes, int]] = []
        for chunk_id, vec in items:
            if vec.shape[-1] != dim:
                raise ValueError(f"vector for {chunk_id!r} has dim={vec.shape[-1]}, expected {dim}")
            rows.append((chunk_id, model, dim, encode_vector(vec), ts))
        if not rows:
            return 0
        conn = self._connect()
        with conn:
            conn.executemany(
                "INSERT INTO embeddings(chunk_id, model, dim, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(chunk_id) DO UPDATE SET "
                "  model = excluded.model, "
                "  dim = excluded.dim, "
                "  vector = excluded.vector, "
                "  created_at = excluded.created_at",
                rows,
            )
        return len(rows)

    def get(self, chunk_id: str) -> tuple[np.ndarray, str, int] | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT vector, model, dim FROM embeddings WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        return decode_vector(row[0], row[2]), row[1], row[2]

    def count(self, *, model: str | None = None) -> int:
        conn = self._connect()
        if model is None:
            row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
            ).fetchone()
        return int(row[0]) if row else 0

    def iter_vectors(
        self, *, model: str, batch_size: int = 1024
    ) -> Iterator[tuple[list[str], np.ndarray]]:
        """Yield ``(chunk_ids, matrix)`` chunks for HNSW bulk load.

        The matrix is shape ``(batch_size, dim)`` (last batch may be smaller).
        We stream rather than `fetchall()` because at 1M chunks the full
        materialisation is ~3 GB; HNSW only needs one batch in flight.
        """
        conn = self._connect()
        cur = conn.execute(
            "SELECT chunk_id, vector, dim FROM embeddings WHERE model = ? ORDER BY chunk_id",
            (model,),
        )
        ids: list[str] = []
        vecs: list[np.ndarray] = []
        dim_seen: int | None = None
        for chunk_id, blob, dim in cur:
            if dim_seen is None:
                dim_seen = dim
            elif dim != dim_seen:
                raise ValueError(
                    f"mixed dims for model={model!r}: row {chunk_id} dim={dim} "
                    f"!= first-row dim={dim_seen}"
                )
            ids.append(chunk_id)
            vecs.append(decode_vector(blob, dim))
            if len(ids) >= batch_size:
                yield ids, np.stack(vecs, axis=0)
                ids, vecs = [], []
        if ids:
            yield ids, np.stack(vecs, axis=0)

    def delete_by_chunk_ids(self, chunk_ids: Iterable[str]) -> int:
        ids = list(chunk_ids)
        if not ids:
            return 0
        conn = self._connect()
        with conn:
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"DELETE FROM embeddings WHERE chunk_id IN ({placeholders})",
                ids,
            )
            return cur.rowcount

    def delete_for_model(self, model: str) -> int:
        conn = self._connect()
        with conn:
            cur = conn.execute("DELETE FROM embeddings WHERE model = ?", (model,))
            return cur.rowcount

    def stats(self) -> dict[str, tuple[int, int]]:
        """Return ``{model: (count, dim)}`` for every distinct model."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT model, COUNT(*), MAX(dim), MIN(dim) FROM embeddings GROUP BY model"
        ).fetchall()
        out: dict[str, tuple[int, int]] = {}
        for model, n, dmax, dmin in rows:
            if dmax != dmin:
                raise ValueError(f"embeddings for model={model!r} have mixed dims [{dmin}, {dmax}]")
            out[model] = (int(n), int(dmax))
        return out
