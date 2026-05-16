"""Embed text chunks with a sentence-transformers model (default: bge-en-v1.5)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np

from reposage.config import get_settings


class Embedder:
    """Thin wrapper that lazy-loads a sentence-transformers model."""

    def __init__(self, model_name: str | None = None, device: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embed_model
        self.device = device or settings.embed_device
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            # Phase 1: actually instantiate SentenceTransformer.
            raise NotImplementedError
        return self._model

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return a `(len(texts), embed_dim)` float32 array."""
        _ = self._load()
        raise NotImplementedError

    def embed_iter(self, texts: Iterable[str], batch_size: int = 64) -> Iterable[np.ndarray]:
        """Stream embeddings batch-by-batch (Phase 1 implementation)."""
        raise NotImplementedError
