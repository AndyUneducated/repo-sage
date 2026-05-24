"""Cross-encoder reranker (default: ``BAAI/bge-reranker-v2-m3``).

A cross-encoder scores ``(query, candidate)`` pairs jointly, which is ~5x
better than RRF in our offline tests on hybrid output but ~50x more
expensive per pair. Phase 2 caps the candidate pool at 20 to keep us
inside the 1.5 s end-to-end P50 budget; Phase 6 will batch.

`MockReranker` is a deterministic stand-in used by:
    * unit/integration tests that should not download a 350 MB model;
    * the "mock" LLM mode for CI without secrets — paired with `HashEmbedder`
      and `LocalDenseIndex` it lets the entire `/ask` pipeline run offline.
"""

from __future__ import annotations

from collections.abc import Sequence

from reposage.config import get_settings
from reposage.retrieval.protocols import ScoredId


class CrossEncoderReranker:
    """Production reranker backed by sentence-transformers' `CrossEncoder`."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.reranker_model
        self._model: object | None = None

    @property
    def model(self) -> str:
        return self.model_name

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[str, str]],
        top_k: int = 8,
    ) -> list[ScoredId]:
        if not candidates:
            return []
        ce = self._load()
        pairs = [[query, text] for _, text in candidates]
        scores = ce.predict(pairs)  # type: ignore[attr-defined]
        scored = [
            ScoredId(chunk_id=cid, score=float(s))
            for (cid, _), s in zip(candidates, scores, strict=False)
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


class MockReranker:
    """Deterministic reranker that scores by lexical overlap.

    Score is the count of unique query tokens that appear in the chunk text.
    This is *not* a quality signal — it's enough to drive reranker plumbing
    tests and to give the mock end-to-end pipeline non-trivial ordering.
    """

    def __init__(self, model_name: str = "mock-reranker-v1") -> None:
        self._model = model_name

    @property
    def model(self) -> str:
        return self._model

    def rerank(
        self,
        query: str,
        candidates: Sequence[tuple[str, str]],
        top_k: int = 8,
    ) -> list[ScoredId]:
        if not candidates:
            return []
        q_tokens = {t for t in query.lower().split() if t}
        scored = []
        for cid, text in candidates:
            text_tokens = set(text.lower().split())
            scored.append(ScoredId(chunk_id=cid, score=float(len(q_tokens & text_tokens))))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]
