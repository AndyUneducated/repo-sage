"""SQLite adjacency-list store for the symbol graph + repo/file metadata.

Schema (Phase 1):

    nodes(fqn TEXT PK, kind TEXT, language TEXT, repo TEXT, path TEXT,
          start_line INT, end_line INT)
    edges(src TEXT, dst TEXT, kind TEXT, src_path TEXT, src_line INT,
          weight INTEGER DEFAULT 1,
          PRIMARY KEY (src, dst, kind, src_line))
    INDEX edges_dst_kind ON edges(dst, kind)        -- reverse adjacency
    INDEX edges_src_kind ON edges(src, kind)
    repo_meta(repo TEXT PK, head_sha TEXT, default_branch TEXT,
              last_indexed_at INTEGER)
    file_meta(repo TEXT, path TEXT, file_sha TEXT, mtime INTEGER,
              parse_status TEXT, last_indexed_at INTEGER,
              PRIMARY KEY (repo, path))

The store is intentionally not thread-safe at the connection level (SQLite
itself is fine, but we share one connection per `IndexPipeline` to keep the
write path simple). Reads from another process always work.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from reposage.indexer.symbol_graph import EdgeKind, SymbolEdge, SymbolNode

SCHEMA_VERSION = 1


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes(
  fqn        TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  language   TEXT NOT NULL,
  repo       TEXT NOT NULL,
  path       TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges(
  src       TEXT NOT NULL,
  dst       TEXT NOT NULL,
  kind      TEXT NOT NULL,
  src_path  TEXT NOT NULL,
  src_line  INTEGER NOT NULL,
  weight    INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (src, dst, kind, src_line)
);
CREATE INDEX IF NOT EXISTS edges_dst_kind ON edges(dst, kind);
CREATE INDEX IF NOT EXISTS edges_src_kind ON edges(src, kind);

CREATE TABLE IF NOT EXISTS repo_meta(
  repo             TEXT PRIMARY KEY,
  head_sha         TEXT,
  default_branch   TEXT,
  last_indexed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS file_meta(
  repo             TEXT NOT NULL,
  path             TEXT NOT NULL,
  file_sha         TEXT NOT NULL,
  mtime            INTEGER NOT NULL,
  parse_status     TEXT NOT NULL,
  last_indexed_at  INTEGER NOT NULL,
  PRIMARY KEY (repo, path)
);
"""


class SQLiteSymbolGraphStore:
    """Persistent symbol graph backed by a single SQLite file.

    The connection is created lazily on first use and held until `close()`
    or the object is garbage-collected. Multiple stores can point at the same
    DB file (they share schema).
    """

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
        # Stamp schema version using SQLite's built-in user_version pragma.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()

    # ---------------------------------------------------------------- nodes

    def upsert_nodes(self, nodes: Iterable[SymbolNode]) -> int:
        rows = [
            (n.fqn, n.kind, n.language, n.repo, n.path, n.start_line, n.end_line) for n in nodes
        ]
        if not rows:
            return 0
        conn = self._connect()
        with conn:
            conn.executemany(
                "INSERT INTO nodes(fqn, kind, language, repo, path, start_line, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(fqn) DO UPDATE SET "
                "  kind = excluded.kind, "
                "  language = excluded.language, "
                "  repo = excluded.repo, "
                "  path = excluded.path, "
                "  start_line = excluded.start_line, "
                "  end_line = excluded.end_line",
                rows,
            )
        return len(rows)

    def upsert_edges(self, edges: Iterable[SymbolEdge]) -> int:
        rows = [(e.src, e.dst, e.kind, e.src_path, e.src_line, 1) for e in edges]
        if not rows:
            return 0
        conn = self._connect()
        with conn:
            # ON CONFLICT increments weight so multiple identical edges
            # at the same site collapse to one row with a real count —
            # used by Phase 3 Leiden weighting.
            conn.executemany(
                "INSERT INTO edges(src, dst, kind, src_path, src_line, weight) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(src, dst, kind, src_line) DO UPDATE SET "
                "  weight = weight + 1",
                rows,
            )
        return len(rows)

    # --- query helpers (graph route hot path) -----------------------------

    def callers_of(self, fqn: str) -> list[SymbolEdge]:
        return self.edges(fqn, kind="call", direction="in")

    def callees_of(self, fqn: str) -> list[SymbolEdge]:
        return self.edges(fqn, kind="call", direction="out")

    def edges(
        self, fqn: str, kind: EdgeKind | None = None, direction: str = "out"
    ) -> list[SymbolEdge]:
        if direction not in {"in", "out"}:
            raise ValueError(f"direction must be 'in' or 'out', got {direction!r}")
        conn = self._connect()
        col = "dst" if direction == "in" else "src"
        sql = f"SELECT src, dst, kind, src_path, src_line FROM edges WHERE {col} = ?"
        params: tuple[object, ...] = (fqn,)
        if kind is not None:
            sql += " AND kind = ?"
            params = (fqn, kind)
        sql += " ORDER BY src_path, src_line"
        rows = conn.execute(sql, params).fetchall()
        return [
            SymbolEdge(src=r[0], dst=r[1], kind=r[2], src_path=r[3], src_line=r[4]) for r in rows
        ]

    def get_node(self, fqn: str) -> SymbolNode | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT fqn, kind, language, repo, path, start_line, end_line FROM nodes WHERE fqn = ?",
            (fqn,),
        ).fetchone()
        if row is None:
            return None
        return SymbolNode(
            fqn=row[0],
            kind=row[1],
            language=row[2],
            repo=row[3],
            path=row[4],
            start_line=row[5],
            end_line=row[6],
        )

    def find_nodes_by_suffix(self, suffix: str, limit: int = 32) -> list[SymbolNode]:
        """Find nodes whose FQN ends with ``suffix`` or equals it.

        Used by the graph fast-path to map a bare ``Class.method`` from the
        user's question to all matching FQNs.
        """
        conn = self._connect()
        # Match either `pkg.foo.<suffix>` (suffix match with leading dot) or
        # the exact FQN.
        like = f"%.{suffix}"
        rows = conn.execute(
            "SELECT fqn, kind, language, repo, path, start_line, end_line FROM nodes "
            "WHERE fqn = ? OR fqn LIKE ? "
            "ORDER BY fqn LIMIT ?",
            (suffix, like, limit),
        ).fetchall()
        return [
            SymbolNode(
                fqn=r[0],
                kind=r[1],
                language=r[2],
                repo=r[3],
                path=r[4],
                start_line=r[5],
                end_line=r[6],
            )
            for r in rows
        ]

    # --- repo / file meta -------------------------------------------------

    def upsert_repo_meta(
        self,
        repo: str,
        head_sha: str | None = None,
        default_branch: str | None = None,
        timestamp: int | None = None,
    ) -> None:
        ts = timestamp if timestamp is not None else int(time.time())
        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO repo_meta(repo, head_sha, default_branch, last_indexed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET "
                "  head_sha = excluded.head_sha, "
                "  default_branch = excluded.default_branch, "
                "  last_indexed_at = excluded.last_indexed_at",
                (repo, head_sha, default_branch, ts),
            )

    def upsert_file_meta(
        self,
        repo: str,
        path: str,
        file_sha: str,
        mtime: int,
        parse_status: str,
        timestamp: int | None = None,
    ) -> None:
        ts = timestamp if timestamp is not None else int(time.time())
        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO file_meta(repo, path, file_sha, mtime, parse_status, last_indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, path) DO UPDATE SET "
                "  file_sha = excluded.file_sha, "
                "  mtime = excluded.mtime, "
                "  parse_status = excluded.parse_status, "
                "  last_indexed_at = excluded.last_indexed_at",
                (repo, path, file_sha, mtime, parse_status, ts),
            )

    def get_file_sha(self, repo: str, path: str) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT file_sha FROM file_meta WHERE repo = ? AND path = ?",
            (repo, path),
        ).fetchone()
        return row[0] if row else None

    def parse_status_counts(self, repo: str) -> dict[str, int]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT parse_status, COUNT(*) FROM file_meta WHERE repo = ? GROUP BY parse_status",
            (repo,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def clear_repo(self, repo: str) -> None:
        """Delete all rows for a repo across `nodes`, `edges`, and `file_meta`.

        Used by `IndexPipeline` when a force-rebuild is requested. `repo_meta`
        and external `chunks` are handled by their owning stores.
        """
        conn = self._connect()
        with conn:
            conn.execute("DELETE FROM nodes WHERE repo = ?", (repo,))
            # `edges` doesn't carry repo directly, but every edge's src_path is
            # owned by some node — we delete edges whose src_path appears only
            # in `file_meta` rows we're about to drop.
            conn.execute(
                "DELETE FROM edges WHERE src_path IN (  SELECT path FROM file_meta WHERE repo = ?)",
                (repo,),
            )
            conn.execute("DELETE FROM file_meta WHERE repo = ?", (repo,))
