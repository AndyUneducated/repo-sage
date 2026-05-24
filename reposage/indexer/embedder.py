"""Sentence-transformers embedder + a deterministic test fake.

`BgeEmbedder` lazy-loads `BAAI/bge-en-v1.5` (768-d) the first time `embed()`
is called, so importing this module (or constructing the embedder for type
hints) never triggers a multi-hundred-MB download.

`HashEmbedder` is a deterministic, dependency-free fake used by:

    * Phase 2 unit / integration tests where loading bge would dwarf the
      test time and require network access in CI.
    * The "mock" LLM mode (CI without API keys) so the full ask pipeline
      is exercisable end-to-end.

Hash embeddings are not semantically meaningful, but they are deterministic,
unit-norm, and have the configured `dim`, which is enough to validate the
plumbing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from reposage.config import get_settings


class EmbeddingProvider(Protocol):
    """Minimal contract every embedder must satisfy."""

    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return a `(len(texts), dim)` float32 array (rows L2-normalised)."""
        ...


class BgeEmbedder:
    """Production embedder backed by sentence-transformers."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embed_model
        self._device = device or settings.embed_device
        self._batch_size = batch_size
        self._dim = settings.embed_dim
        self._model: object | None = None

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        st = SentenceTransformer(self._model_name, device=self._device)
        # Cross-check the configured dim against the loaded weights so a
        # stale `embed_dim` setting fails loudly instead of silently writing
        # truncated vectors.
        actual = int(st.get_sentence_embedding_dimension() or 0)
        if actual and actual != self._dim:
            raise RuntimeError(
                f"embed_dim mismatch for {self._model_name!r}: "
                f"settings.embed_dim={self._dim}, model={actual}"
            )
        self._model = st
        return st

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        st = self._load()
        # `encode` returns a (n, dim) numpy array when convert_to_numpy=True.
        arr: np.ndarray = st.encode(  # type: ignore[attr-defined]
            list(texts),
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        if arr.dtype != np.float32:
            arr = arr.astype(np.float32, copy=False)
        return arr


class HashEmbedder:
    """Deterministic stand-in for `BgeEmbedder`.

    Each text gets a sha256-derived seed; the seed drives a `default_rng`
    that produces a unit-norm gaussian vector of the configured `dim`. Same
    text → same vector across processes and machines. Enough to drive the
    Phase 2 plumbing tests without any model download.
    """

    def __init__(self, dim: int | None = None, model: str = "hash-embedder-v1") -> None:
        self._dim = dim or get_settings().embed_dim
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "little", signed=False)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self._dim).astype(np.float32)
        norm = float(np.linalg.norm(v))
        if norm == 0.0:
            return v
        return v / norm

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return np.stack([self._embed_one(t) for t in texts], axis=0)


# Backwards-compatible alias kept for code that imported the old stub name.
Embedder = BgeEmbedder
