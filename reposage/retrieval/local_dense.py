"""In-process numpy-only `DenseRetriever` for tests and the mock LLM mode.

Why ship a non-HNSW dense index at all:

* Python unit tests must never depend on a running Go binary or a 350 MB
  cross-encoder download. A linear scan over <10k 768-d float32 vectors is
  ~10 ms on CPU, so it's actually faster than the gRPC round trip.
* The eval-gate workflow runs without LLM secrets via the mock LLM and the
  HashEmbedder; pairing those with `LocalDenseIndex` means the entire
  `/ask` pipeline can be exercised end-to-end in CI with zero secrets.

Everything else (`HnswGrpcClient`) is the production path and is what the
serving stack actually uses.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from reposage.retrieval.protocols import ScoredId


class LocalDenseIndex:
    """O(N*d) brute-force top-k via cosine similarity over normalised vectors."""

    def __init__(self, model: str, dim: int) -> None:
        self._model = model
        self._dim = dim
        self._ids: list[str] = []
        self._matrix: np.ndarray | None = None  # (N, dim) float32

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def add(self, chunk_ids: Sequence[str], vectors: np.ndarray) -> None:
        if vectors.shape[0] != len(chunk_ids):
            raise ValueError(f"id/vector count mismatch: {len(chunk_ids)} vs {vectors.shape[0]}")
        if vectors.shape[1] != self._dim:
            raise ValueError(f"vector dim {vectors.shape[1]} != index dim {self._dim}")
        if vectors.dtype != np.float32:
            vectors = vectors.astype(np.float32, copy=False)
        # Normalise once at insert time so search is a pure dot product.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        vectors = vectors / norms
        if self._matrix is None:
            self._matrix = vectors.copy()
            self._ids = list(chunk_ids)
        else:
            self._matrix = np.concatenate([self._matrix, vectors], axis=0)
            self._ids.extend(chunk_ids)

    def __len__(self) -> int:
        return 0 if self._matrix is None else self._matrix.shape[0]

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 50,
        ef_search: int | None = None,
    ) -> list[ScoredId]:
        del ef_search  # parity with HNSW; ignored by the brute-force scanner
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
        # Convert cosine similarity to a "lower is closer" distance to match
        # the HNSW gRPC contract.
        distances = 1.0 - sims
        k = min(top_k, distances.shape[0])
        # argpartition is O(N) for the unsorted top-k slice.
        idx = np.argpartition(distances, kth=k - 1)[:k]
        # Then sort just the survivors.
        idx = idx[np.argsort(distances[idx])]
        return [ScoredId(self._ids[i], float(distances[i])) for i in idx]

    async def healthcheck(self) -> bool:
        return True
