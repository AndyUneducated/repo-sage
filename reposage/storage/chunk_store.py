"""SQLite-backed store for `Chunk` rows produced by `Chunker`.

The store deliberately uses the same SQLite file as `SQLiteSymbolGraphStore`
so a single `data/reposage.db` is the index for an entire repo. Phase 2 will
add an embedding store keyed on `chunk_id` from this table.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from reposage.indexer.chunker import Chunk

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunks(
  chunk_id      TEXT PRIMARY KEY,
  repo          TEXT NOT NULL,
  path          TEXT NOT NULL,
  language      TEXT NOT NULL,
  start_line    INTEGER NOT NULL,
  end_line      INTEGER NOT NULL,
  symbol        TEXT,
  parent_symbol TEXT,
  text          TEXT NOT NULL,
  file_sha      TEXT NOT NULL,
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_repo_path ON chunks(repo, path);
CREATE INDEX IF NOT EXISTS chunks_symbol ON chunks(symbol);
"""


class ChunkStore:
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
        # Phase 2 added `embeddings` with `chunk_id REFERENCES chunks(chunk_id)
        # ON DELETE CASCADE`. Cascade only fires when foreign_keys is ON on
        # the connection that runs the DELETE.
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

    def upsert(self, chunks: Iterable[Chunk], file_sha: str, timestamp: int | None = None) -> int:
        ts = timestamp if timestamp is not None else int(time.time())
        rows = [
            (
                c.chunk_id,
                c.repo,
                str(c.path),
                c.language,
                c.start_line,
                c.end_line,
                c.symbol,
                c.parent_symbol,
                c.text,
                file_sha,
                ts,
            )
            for c in chunks
        ]
        if not rows:
            return 0
        conn = self._connect()
        with conn:
            conn.executemany(
                "INSERT INTO chunks(chunk_id, repo, path, language, start_line, end_line, "
                "                   symbol, parent_symbol, text, file_sha, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chunk_id) DO UPDATE SET "
                "  repo = excluded.repo, "
                "  path = excluded.path, "
                "  language = excluded.language, "
                "  start_line = excluded.start_line, "
                "  end_line = excluded.end_line, "
                "  symbol = excluded.symbol, "
                "  parent_symbol = excluded.parent_symbol, "
                "  text = excluded.text, "
                "  file_sha = excluded.file_sha, "
                "  created_at = excluded.created_at",
                rows,
            )
        return len(rows)

    def delete_by_path(self, repo: str, path: str) -> int:
        conn = self._connect()
        with conn:
            cur = conn.execute("DELETE FROM chunks WHERE repo = ? AND path = ?", (repo, path))
            return cur.rowcount

    def clear_repo(self, repo: str) -> int:
        conn = self._connect()
        with conn:
            cur = conn.execute("DELETE FROM chunks WHERE repo = ?", (repo,))
            return cur.rowcount

    def iter_for_repo(self, repo: str) -> Iterable[Chunk]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT chunk_id, repo, path, language, start_line, end_line, "
            "       symbol, parent_symbol, text "
            "FROM chunks WHERE repo = ? ORDER BY path, start_line",
            (repo,),
        )
        for r in rows:
            yield Chunk(
                chunk_id=r[0],
                repo=r[1],
                path=Path(r[2]),
                language=r[3],
                start_line=r[4],
                end_line=r[5],
                symbol=r[6],
                parent_symbol=r[7],
                text=r[8],
            )
