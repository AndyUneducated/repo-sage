"""BM25 sparse retrieval over chunk text.

Phase 2 ships ``rank-bm25`` (pure Python). Phase 6 swaps in Tantivy for
~10x indexing throughput; the `SparseRetriever` Protocol is the migration
boundary and `reposage.retrieval.tokenize.tokenize` is the shared contract both
backends use so recall does not drift on the swap (DD-035).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rank_bm25 import BM25Okapi

from reposage.retrieval.protocols import ScoredId
from reposage.retrieval.tokenize import tokenize

__all__ = ["BM25SparseRetriever", "tokenize"]


class BM25SparseRetriever:
    """In-memory BM25 over chunk text, cold-loaded from `chunks` SQLite table."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._bm25: BM25Okapi | None = None

    @classmethod
    def from_sqlite(cls, db_path: Path, repo: str | None = None) -> BM25SparseRetriever:
        """Build a fresh index by scanning `chunks` for the given repo."""
        idx = cls()
        idx.load(db_path, repo=repo)
        return idx

    def load(self, db_path: Path, *, repo: str | None = None) -> None:
        conn = sqlite3.connect(db_path)
        try:
            if repo is None:
                rows = conn.execute("SELECT chunk_id, text FROM chunks ORDER BY chunk_id")
            else:
                rows = conn.execute(
                    "SELECT chunk_id, text FROM chunks WHERE repo = ? ORDER BY chunk_id",
                    (repo,),
                )
            ids: list[str] = []
            tokens: list[list[str]] = []
            for chunk_id, text in rows:
                ids.append(chunk_id)
                tokens.append(tokenize(text))
        finally:
            conn.close()
        self.fit(ids, tokens)

    def fit(self, ids: Sequence[str], tokens: Sequence[Sequence[str]]) -> None:
        if len(ids) != len(tokens):
            raise ValueError("ids/tokens length mismatch")
        # rank-bm25 explodes on a fully empty corpus; substitute a single
        # placeholder so search still returns gracefully.
        if not ids:
            self._ids = []
            self._bm25 = None
            return
        self._ids = list(ids)
        # Replace empty token lists with a sentinel so BM25Okapi can
        # compute idf without dividing by zero. Empty docs simply never
        # score against any term.
        non_empty = [list(t) if t else ["__empty_chunk__"] for t in tokens]
        self._bm25 = BM25Okapi(non_empty)

    def __len__(self) -> int:
        return len(self._ids)

    async def search(self, query: str, top_k: int = 50) -> list[ScoredId]:
        if not self._ids or self._bm25 is None:
            return []
        q = tokenize(query)
        if not q:
            return []
        scores = self._bm25.get_scores(q)
        # argpartition for O(n) top-k slice, then sort the survivors.
        if len(scores) <= top_k:
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        else:
            # Numpy is not imported here on purpose; we keep this module
            # numpy-free so it stays import-cheap and Phase 7 swap is
            # surgical.
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
            order = top_indices
        out: list[ScoredId] = []
        for i in order:
            if scores[i] <= 0.0:
                # BM25 returns 0 for docs that share no terms with the query.
                # Filter them so RRF fusion only sees actually-matched docs.
                break
            out.append(ScoredId(chunk_id=self._ids[i], score=float(scores[i])))
            if len(out) >= top_k:
                break
        return out


# Backwards-compatible alias for the Phase 1 stub name.
BM25Index = BM25SparseRetriever
