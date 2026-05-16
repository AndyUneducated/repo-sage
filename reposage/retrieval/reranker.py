"""Cross-encoder reranker (default: `BAAI/bge-reranker-v2-m3`)."""

from __future__ import annotations

from collections.abc import Sequence

from reposage.config import get_settings
from reposage.retrieval.hybrid import RetrievedChunk


class CrossEncoderReranker:
    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.reranker_model
        self._model: object | None = None

    def rerank(self, query: str, chunks: Sequence[RetrievedChunk], top_k: int = 8) -> list[RetrievedChunk]:
        raise NotImplementedError
