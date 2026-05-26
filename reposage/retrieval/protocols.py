"""Retrieval-side Protocols — the seams Phase 5/6/7/8 swap implementations at.

Keeping them in one file makes the contract obvious and lets unit tests use
trivial in-process fakes (`LocalDenseIndex`, `HashEmbedder`, mock LLM)
without needing a Go binary or network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class ScoredId:
    chunk_id: str
    score: float


class DenseRetriever(Protocol):
    """Vector top-k. Phase 2: gRPC client to go-hnsw. Phase 8: sharded."""

    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def search(
        self, query_vector: Sequence[float], top_k: int = 50, ef_search: int | None = None
    ) -> list[ScoredId]: ...

    async def healthcheck(self) -> bool: ...


class SparseRetriever(Protocol):
    """Lexical top-k. Phase 2: rank-bm25 in-process. Phase 7: Tantivy."""

    async def search(self, query: str, top_k: int = 50) -> list[ScoredId]: ...


class Reranker(Protocol):
    """Cross-encoder rescore over (query, candidate) pairs."""

    @property
    def model(self) -> str: ...

    def rerank(
        self, query: str, candidates: Sequence[tuple[str, str]], top_k: int = 8
    ) -> list[ScoredId]:
        """Score (chunk_id, text) candidates against query; return top_k."""
        ...


@dataclass(slots=True, frozen=True)
class ChatMessage:
    role: str
    content: str


class LLMClient(Protocol):
    """Provider-agnostic chat completion."""

    @property
    def model(self) -> str: ...

    async def complete(self, messages: Sequence[ChatMessage]) -> str: ...

    async def aclose(self) -> None:
        """Drain any provider-side background work before the event loop closes.

        LiteLLM enqueues internal success/failure handlers on a global async
        queue bound to the *first* event loop it sees. Short-lived loops (e.g.
        ``asyncio.run(...)`` in CLI / benchmarks / tests) close before that
        queue drains, leaving orphaned coroutines that trigger
        ``RuntimeWarning: coroutine was never awaited``. Concrete clients
        opt-in to a one-shot drain here; in-process mocks are a no-op.
        """
        ...


@dataclass(slots=True, frozen=True)
class ScoredCommunity:
    """One scored community returned by a `CommunityRetriever`."""

    community_id: int
    level: int
    title: str | None
    summary: str | None
    score: float  # higher is more relevant


class CommunityRetriever(Protocol):
    """Phase 3 GraphRAG community retrieval seam.

    Phase 3 ships `LocalCommunityRetriever` (numpy linear scan over
    community vectors). Phase 5 can swap in an HNSW-backed implementation
    by satisfying the same Protocol — `RetrievalService` doesn't care.
    """

    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 3,
    ) -> list[ScoredCommunity]: ...
