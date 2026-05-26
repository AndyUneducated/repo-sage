"""Persistent store for GraphRAG communities + LLM-generated summaries.

The store owns three tables (added in Phase 3, see `docs/INDEX_SCHEMA.md`):

* `communities`        — one row per detected community (any level).
* `community_members`  — community ↔ FQN many-to-many.
* `community_embeddings` — float32 vector per community for the
  `community` retrieval path.

All three share `data/reposage.db` with the rest of the index so a
single file is the complete checkpoint (DD-011).

Why the two-pass upsert: `CommunityDetector` returns *detection-local*
ids (1, 2, 3, ...) and `parent_id` references those local ids. The
`communities` table autoincrement assigns its own ids, so we INSERT
parent-less in topological order (level descending — top-level first),
build a `local_id → community_id` mapping, then re-UPDATE `parent_id`
in a second pass.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from reposage.storage.embeddings_store import decode_vector, encode_vector

if TYPE_CHECKING:
    from reposage.indexer.graphrag.community import Community

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS communities(
  community_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  repo           TEXT NOT NULL,
  level          INTEGER NOT NULL,
  parent_id      INTEGER REFERENCES communities(community_id) ON DELETE SET NULL,
  member_count   INTEGER NOT NULL,
  subtree_size   INTEGER NOT NULL,
  content_sha    TEXT NOT NULL,
  title          TEXT,
  summary        TEXT,
  summary_model  TEXT,
  detected_at    INTEGER NOT NULL,
  summarized_at  INTEGER
);
CREATE INDEX IF NOT EXISTS communities_repo_level  ON communities(repo, level);
CREATE INDEX IF NOT EXISTS communities_parent      ON communities(parent_id);
CREATE INDEX IF NOT EXISTS communities_content_sha ON communities(repo, content_sha);

CREATE TABLE IF NOT EXISTS community_members(
  community_id INTEGER NOT NULL REFERENCES communities(community_id) ON DELETE CASCADE,
  fqn          TEXT NOT NULL,
  is_seed      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (community_id, fqn)
);
CREATE INDEX IF NOT EXISTS community_members_fqn ON community_members(fqn);

CREATE TABLE IF NOT EXISTS community_embeddings(
  community_id INTEGER PRIMARY KEY REFERENCES communities(community_id) ON DELETE CASCADE,
  model        TEXT NOT NULL,
  dim          INTEGER NOT NULL,
  vector       BLOB NOT NULL,
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS community_embeddings_model ON community_embeddings(model);
"""


class CommunityStore:
    """SQLite-backed CRUD for the three GraphRAG tables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    # ----------------------------------------------------- connection mgmt

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        # Required for ON DELETE CASCADE on community_members /
        # community_embeddings to actually fire when a community row is
        # deleted on this connection.
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

    # ---------------------------------------------------- write: upsert

    def clear_repo(self, repo: str) -> int:
        """Drop every community row for ``repo`` (members + embeddings
        cascade away). Returns rows deleted in ``communities``."""
        conn = self._connect()
        with conn:
            cur = conn.execute("DELETE FROM communities WHERE repo = ?", (repo,))
            return cur.rowcount

    def upsert(
        self,
        communities: Iterable[Community],
        *,
        repo: str,
        replace_existing: bool = True,
        timestamp: int | None = None,
    ) -> dict[int, int]:
        """Persist a fresh community partition.

        Returns ``{local_id: community_id}`` so callers (notably the
        summarizer, which also writes embeddings) can translate from
        the detector's local ids.

        When ``replace_existing`` is True (the default for a full
        re-index), all rows for ``repo`` are deleted first. Incremental
        re-indexing in Phase 7 will set this to False and rely on
        per-row `content_sha` cache logic.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        comms = list(communities)
        if not comms:
            if replace_existing:
                self.clear_repo(repo)
            return {}

        conn = self._connect()
        with conn:
            if replace_existing:
                conn.execute("DELETE FROM communities WHERE repo = ?", (repo,))
            local_to_db: dict[int, int] = {}
            # Insert top-level first so that lower-level rows can
            # reference an already-existing parent row immediately if we
            # ever switch to single-pass writes. Two-pass keeps the
            # interface simple for now.
            for c in sorted(comms, key=lambda x: -x.level):
                cur = conn.execute(
                    "INSERT INTO communities("
                    "  repo, level, parent_id, member_count, subtree_size, "
                    "  content_sha, title, summary, summary_model, "
                    "  detected_at, summarized_at"
                    ") VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        repo,
                        c.level,
                        len(c.members),
                        self._subtree_size(c, comms),
                        c.content_sha,
                        c.title,
                        c.summary,
                        c.summary_model,
                        ts,
                        ts if c.summary is not None else None,
                    ),
                )
                local_to_db[c.id] = int(cur.lastrowid or 0)

            # Pass 2: stitch parent_id refs.
            for c in comms:
                if c.parent_id is None:
                    continue
                parent_db = local_to_db.get(c.parent_id)
                if parent_db is None:
                    continue
                conn.execute(
                    "UPDATE communities SET parent_id = ? WHERE community_id = ?",
                    (parent_db, local_to_db[c.id]),
                )

            # Members.
            member_rows = [(local_to_db[c.id], fqn, 0) for c in comms for fqn in c.members]
            if member_rows:
                conn.executemany(
                    "INSERT INTO community_members(community_id, fqn, is_seed) VALUES (?, ?, ?)",
                    member_rows,
                )
        return local_to_db

    def mark_seeds(self, community_id: int, seed_fqns: Iterable[str]) -> int:
        """Flip ``is_seed=1`` for the given (community, fqn) pairs."""
        seeds = list(seed_fqns)
        if not seeds:
            return 0
        conn = self._connect()
        placeholders = ",".join("?" * len(seeds))
        with conn:
            cur = conn.execute(
                f"UPDATE community_members SET is_seed = 1 "
                f"WHERE community_id = ? AND fqn IN ({placeholders})",
                (community_id, *seeds),
            )
            return cur.rowcount

    def update_summary(
        self,
        community_id: int,
        *,
        title: str | None,
        summary: str | None,
        summary_model: str,
        timestamp: int | None = None,
    ) -> None:
        """Attach a summary to an existing community row."""
        ts = timestamp if timestamp is not None else int(time.time())
        conn = self._connect()
        with conn:
            conn.execute(
                "UPDATE communities SET "
                "  title = ?, summary = ?, summary_model = ?, summarized_at = ? "
                "WHERE community_id = ?",
                (title, summary, summary_model, ts, community_id),
            )

    # ---------------------------------------------- write: embeddings

    def upsert_embedding(
        self,
        community_id: int,
        vector: np.ndarray,
        *,
        model: str,
        dim: int,
        timestamp: int | None = None,
    ) -> None:
        if vector.shape[-1] != dim:
            raise ValueError(f"vector dim={vector.shape[-1]} != expected {dim}")
        # Guard: don't embed a community that hasn't been summarised yet —
        # otherwise we'd index a vector for a placeholder summary.
        conn = self._connect()
        row = conn.execute(
            "SELECT summarized_at FROM communities WHERE community_id = ?",
            (community_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"community {community_id} does not exist")
        if row[0] is None:
            raise ValueError(f"refusing to embed community {community_id}: summarized_at IS NULL")
        ts = timestamp if timestamp is not None else int(time.time())
        with conn:
            conn.execute(
                "INSERT INTO community_embeddings(community_id, model, dim, vector, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(community_id) DO UPDATE SET "
                "  model = excluded.model, "
                "  dim = excluded.dim, "
                "  vector = excluded.vector, "
                "  created_at = excluded.created_at",
                (community_id, model, dim, encode_vector(vector), ts),
            )

    # -------------------------------------------------------- read: query

    def find_by_content_sha(self, repo: str, content_sha: str) -> Community | None:
        """Look up an existing community by `(repo, content_sha)`.

        Used by the summarizer to skip re-summarising unchanged
        communities across re-indexes.
        """
        conn = self._connect()
        row = conn.execute(
            "SELECT community_id, level, parent_id, content_sha, title, summary, "
            "       summary_model "
            "FROM communities WHERE repo = ? AND content_sha = ? LIMIT 1",
            (repo, content_sha),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_community(conn, row)

    def find_by_member(self, fqn: str) -> list[Community]:
        """Every community (across levels) that contains ``fqn``."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT c.community_id, c.level, c.parent_id, c.content_sha, "
            "       c.title, c.summary, c.summary_model "
            "FROM communities c "
            "JOIN community_members m ON m.community_id = c.community_id "
            "WHERE m.fqn = ? ORDER BY c.level",
            (fqn,),
        ).fetchall()
        return [self._row_to_community(conn, r) for r in rows]

    def top_level(self, repo: str) -> list[Community]:
        """Communities with no parent for ``repo`` (typically level N)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT community_id, level, parent_id, content_sha, title, summary, "
            "       summary_model "
            "FROM communities WHERE repo = ? AND parent_id IS NULL ORDER BY level DESC, community_id",
            (repo,),
        ).fetchall()
        return [self._row_to_community(conn, r) for r in rows]

    def get(self, community_id: int) -> Community | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT community_id, level, parent_id, content_sha, title, summary, "
            "       summary_model "
            "FROM communities WHERE community_id = ?",
            (community_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_community(conn, row)

    def seed_fqns(self, community_id: int) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT fqn FROM community_members WHERE community_id = ? AND is_seed = 1 ORDER BY fqn",
            (community_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def iter_for_repo(self, repo: str) -> Iterator[Community]:
        """All communities for ``repo`` ordered level-ascending."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT community_id, level, parent_id, content_sha, title, summary, "
            "       summary_model "
            "FROM communities WHERE repo = ? ORDER BY level, community_id",
            (repo,),
        ).fetchall()
        for r in rows:
            yield self._row_to_community(conn, r)

    # ------------------------------------------- read: embeddings

    def iter_embeddings_for_model(
        self, *, model: str, repo: str | None = None
    ) -> Iterator[tuple[int, str | None, str | None, int, np.ndarray]]:
        """Yield `(community_id, title, summary, level, vector)` rows for
        the given embedding model.

        Used by `LocalCommunityRetriever` at boot to populate its in-memory
        matrix.
        """
        conn = self._connect()
        if repo is None:
            sql = (
                "SELECT ce.community_id, c.title, c.summary, c.level, ce.vector, ce.dim "
                "FROM community_embeddings ce "
                "JOIN communities c USING(community_id) "
                "WHERE ce.model = ? ORDER BY ce.community_id"
            )
            params: tuple[object, ...] = (model,)
        else:
            sql = (
                "SELECT ce.community_id, c.title, c.summary, c.level, ce.vector, ce.dim "
                "FROM community_embeddings ce "
                "JOIN communities c USING(community_id) "
                "WHERE ce.model = ? AND c.repo = ? ORDER BY ce.community_id"
            )
            params = (model, repo)
        for cid, title, summary, level, blob, dim in conn.execute(sql, params):
            yield cid, title, summary, level, decode_vector(blob, dim)

    def stats(self, *, repo: str) -> dict[str, int]:
        """Per-level counts for one repo. Used by the indexer manifest."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT level, COUNT(*) FROM communities WHERE repo = ? GROUP BY level",
            (repo,),
        ).fetchall()
        out: dict[str, int] = {f"level_{lvl}": int(n) for lvl, n in rows}
        out["total"] = sum(out.values())
        return out

    # ---------------------------------------------------- private helpers

    @staticmethod
    def _subtree_size(c: Community, all_comms: list[Community]) -> int:
        """Sum of leaf members reachable from ``c`` (inclusive).

        For level-0 communities this is `len(c.members)`. For higher
        levels we sum child counts recursively. Used as a ranking
        signal — bigger subtrees usually dominate answers.
        """
        if c.level == 0:
            return len(c.members)
        by_local: dict[int, Community] = {x.id: x for x in all_comms}
        seen: set[int] = set()
        total = 0
        stack: list[int] = list(c.child_ids)
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            child = by_local.get(cid)
            if child is None:
                continue
            if child.level == 0:
                total += len(child.members)
            else:
                stack.extend(child.child_ids)
        return max(total, len(c.members))

    def _row_to_community(self, conn: sqlite3.Connection, row: tuple[Any, ...]) -> Community:
        # Lazy import to avoid a circular dependency between
        # `reposage.storage` and `reposage.indexer.graphrag`.
        from reposage.indexer.graphrag.community import (  # noqa: PLC0415
            Community as _Community,
        )

        community_id, level, parent_id, content_sha, title, summary, summary_model = row
        members = tuple(
            r[0]
            for r in conn.execute(
                "SELECT fqn FROM community_members WHERE community_id = ? ORDER BY fqn",
                (community_id,),
            ).fetchall()
        )
        return _Community(
            id=community_id,
            members=members,
            level=level,
            parent_id=parent_id,
            content_sha=content_sha,
            title=title,
            summary=summary,
            summary_model=summary_model,
        )
