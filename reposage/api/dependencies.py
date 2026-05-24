"""FastAPI dependency wiring for Phase 2 retrieval.

Tests override `get_retrieval_service` via `app.dependency_overrides` so a
single integration test can swap in `LocalDenseIndex` + `MockLLMClient`
without touching the production constructor path.
"""

from __future__ import annotations

import os
from functools import lru_cache

from reposage.config import get_settings
from reposage.indexer.embedder import BgeEmbedder, EmbeddingProvider, HashEmbedder
from reposage.llm.client import LiteLLMClient, MockLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.hnsw_client import HnswGrpcClient
from reposage.retrieval.protocols import DenseRetriever, LLMClient, Reranker
from reposage.retrieval.reranker import CrossEncoderReranker, MockReranker
from reposage.services.retrieval_service import RetrievalService

# Set REPOSAGE_LLM_PROVIDER=mock when running CI / smoke tests without
# secrets. The mock pipeline (HashEmbedder + LocalDenseIndex via the
# constructor below + MockLLMClient + MockReranker) is bit-exact with the
# production wiring so /ask exercises the same code path.
_MOCK_FLAG = "REPOSAGE_LLM_PROVIDER"


def _is_mock() -> bool:
    return os.environ.get(_MOCK_FLAG, "").lower() in {"mock", "test", "stub"}


def build_embedder() -> EmbeddingProvider:
    if _is_mock():
        return HashEmbedder()
    return BgeEmbedder()


def build_llm() -> LLMClient:
    if _is_mock():
        return MockLLMClient()
    return LiteLLMClient()


def build_reranker() -> Reranker:
    if _is_mock():
        return MockReranker()
    return CrossEncoderReranker()


def build_dense() -> DenseRetriever:
    # In mock mode we still return the gRPC client by default so prod
    # code paths get exercised. Tests that don't want a Go binary should
    # use `app.dependency_overrides[get_retrieval_service]` to inject a
    # fully fake service.
    return HnswGrpcClient()


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    embedder = build_embedder()
    sparse = BM25SparseRetriever.from_sqlite(settings.sqlite_path)
    return RetrievalService(
        sqlite_path=settings.sqlite_path,
        embedder=embedder,
        dense=build_dense(),
        sparse=sparse,
        reranker=build_reranker(),
        llm=build_llm(),
    )


def reset_retrieval_service() -> None:
    """Drop the cached service. Tests call this to force a rebuild."""
    get_retrieval_service.cache_clear()
