"""In-process community retriever — numpy linear scan over community
summary embeddings.

Why a linear scanner is enough for Phase 3:

* A 50 kLOC Python repo produces ~50-300 communities (Leiden at default
  resolution). A query against a (200, 768) float32 matrix is one dot
  product - sub-millisecond on CPU. HNSW would be over-engineered.
* The community store keeps vectors in the same SQLite DB as everything
  else (DD-011 multi-model story), so reloading on boot is one SELECT.
* Phase 5 can swap in `HnswCommunityRetriever` by satisfying the same
  `CommunityRetriever` Protocol — `RetrievalService` is unaffected.

The retriever expects vectors to be L2-normalised already (the indexer
writes normalised bge embeddings). We re-normalise at insert just in
case a future model isn't.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from reposage.retrieval.protocols import CommunityRetriever, ScoredCommunity
from reposage.storage.community_store import CommunityStore


class LocalCommunityRetriever(CommunityRetriever):
    """Brute-force cosine-similarity retriever over community vectors."""

    def __init__(self, model: str, dim: int) -> None:
        self._model = model
        self._dim = dim
        self._ids: list[int] = []
        self._levels: list[int] = []
        self._titles: list[str | None] = []
        self._summaries: list[str | None] = []
        self._matrix: np.ndarray | None = None  # (N, dim) float32, row-normalised

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def __len__(self) -> int:
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    # ------------------------------------------------------------- build

    def add(
        self,
        rows: Sequence[tuple[int, str | None, str | None, int, np.ndarray]],
    ) -> None:
        """Append rows of ``(community_id, title, summary, level, vector)``."""
        if not rows:
            return
        new_ids = [r[0] for r in rows]
        new_titles = [r[1] for r in rows]
        new_summaries = [r[2] for r in rows]
        new_levels = [r[3] for r in rows]
        vecs = np.stack([np.asarray(r[4], dtype=np.float32) for r in rows], axis=0)
        if vecs.shape[1] != self._dim:
            raise ValueError(f"vector dim {vecs.shape[1]} != index dim {self._dim}")
        # Defensive re-normalisation; bge already returns unit vectors but
        # community summaries could come from any future encoder.
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        vecs = (vecs / norms).astype(np.float32, copy=False)

        if self._matrix is None:
            self._matrix = vecs
            self._ids = new_ids
            self._levels = new_levels
            self._titles = new_titles
            self._summaries = new_summaries
        else:
            self._matrix = np.concatenate([self._matrix, vecs], axis=0)
            self._ids.extend(new_ids)
            self._levels.extend(new_levels)
            self._titles.extend(new_titles)
            self._summaries.extend(new_summaries)

    @classmethod
    def from_sqlite(
        cls,
        path: Path,
        *,
        model: str,
        dim: int,
        repo: str | None = None,
    ) -> LocalCommunityRetriever:
        """Build an in-memory retriever by streaming rows from SQLite."""
        idx = cls(model=model, dim=dim)
        store = CommunityStore(path)
        try:
            store.init_schema()
            buffered: list[tuple[int, str | None, str | None, int, np.ndarray]] = []
            for cid, title, summary, level, vec in store.iter_embeddings_for_model(
                model=model, repo=repo
            ):
                buffered.append((cid, title, summary, level, vec))
            if buffered:
                idx.add(buffered)
        finally:
            store.close()
        return idx

    # ------------------------------------------------------------ search

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 3,
    ) -> list[ScoredCommunity]:
        if self._matrix is None or self._matrix.shape[0] == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        if q.shape[0] != self._dim:
            raise ValueError(f"query dim {q.shape[0]} != index dim {self._dim}")
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn
        sims = self._matrix @ q  # (N,)
        k = min(top_k, sims.shape[0])
        # argpartition is O(N) for the unsorted top-k slice; we then sort
        # just those k survivors. Score = cosine similarity, higher is
        # better (note: opposite sign from DenseRetriever's "distance").
        idx = np.argpartition(-sims, kth=k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [
            ScoredCommunity(
                community_id=self._ids[i],
                level=self._levels[i],
                title=self._titles[i],
                summary=self._summaries[i],
                score=float(sims[i]),
            )
            for i in idx
        ]


class _UnavailableCommunityRetriever(CommunityRetriever):
    """Drop-in retriever for environments where GraphRAG isn't indexed.

    Returns an empty hit list. `RetrievalService._answer_community`
    treats an empty result as "no relevant community" and falls back to
    hybrid retrieval.
    """

    def __init__(self, model: str, dim: int) -> None:
        self._model = model
        self._dim = dim

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 3,
    ) -> list[ScoredCommunity]:
        del query_vector, top_k
        return []


def empty_retriever(model: str, dim: int) -> CommunityRetriever:
    """Public factory for the no-op retriever; handy in tests."""
    return _UnavailableCommunityRetriever(model=model, dim=dim)
