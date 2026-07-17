"""Single composition root — the only module that reads `REPOSAGE_PROFILE`.

Everywhere else in the codebase (CLI, FastAPI, benchmarks, tests) builds
its retrieval stack via the helpers here. The three surfaces therefore
cannot drift on "what does mock mean", "is dense local or gRPC", or
"should we use the real LLM". A new profile (e.g. `staging`,
`hosted-anthropic`) costs exactly one entry in `_PROFILES`.

The profile -> backend map:

    mock        HashEmbedder           + MockReranker          + MockLLMClient   + LocalDenseIndex
    local       BgeEmbedder            + CrossEncoderReranker  + LiteLLMClient   + LocalDenseIndex
    production  BgeEmbedder            + CrossEncoderReranker  + LiteLLMClient   + HnswGrpcClient

Differences with the pre-refactor wiring:

* `REPOSAGE_LLM_PROVIDER`, `REPOSAGE_RAG_LLM`, `REPOSAGE_DENSE` are
  deleted. Setting any of them no longer has any effect.
* CLI and `/ask` now agree on dense: under `mock` and `local` both build
  a `LocalDenseIndex` from SQLite; only `production` talks to gRPC.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from reposage.config import get_settings
from reposage.indexer.embedder import (
    BgeEmbedder,
    EmbeddingProvider,
    HashEmbedder,
)
from reposage.llm.client import LiteLLMClient, MockLLMClient
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.community_retriever import (
    LocalCommunityRetriever,
    empty_retriever,
)
from reposage.retrieval.hnsw_client import HnswGrpcClient
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.protocols import (
    CommunityRetriever,
    DenseRetriever,
    LLMClient,
    Reranker,
)
from reposage.retrieval.reranker import CrossEncoderReranker, MockReranker
from reposage.services.answer_cache import AnswerCache
from reposage.services.retrieval_service import RetrievalService
from reposage.storage.embeddings_store import EmbeddingsStore

Profile = Literal["mock", "local", "production"]

_PROFILE_ENV = "REPOSAGE_PROFILE"
_VALID_PROFILES: frozenset[str] = frozenset({"mock", "local", "production"})
# Default chosen so `pytest`, `make test`, and a fresh clone all work
# zero-config. An explicit value outside the enum still raises so typos
# never silently degrade.
_DEFAULT_PROFILE: Profile = "mock"


def current_profile() -> Profile:
    """Return the active profile.

    Reads `REPOSAGE_PROFILE`. Unset => `"mock"` so first-time setup is
    zero-config; any other unrecognised value raises with a remediation
    hint instead of silently degrading.
    """
    raw = os.environ.get(_PROFILE_ENV, "").strip().lower()
    if not raw:
        return _DEFAULT_PROFILE
    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"{_PROFILE_ENV}={raw!r} is not one of {sorted(_VALID_PROFILES)}. See .env.example."
        )
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------- backends


def build_embedder() -> EmbeddingProvider:
    """Embedder for query/document encoding.

    Mock uses `HashEmbedder` (deterministic, dep-free). Local and
    production both use `BgeEmbedder`; the only difference between them
    is the dense backend (see `build_dense`).
    """
    if current_profile() == "mock":
        return HashEmbedder()
    return BgeEmbedder()


def build_reranker() -> Reranker:
    if current_profile() == "mock":
        return MockReranker()
    return CrossEncoderReranker()


def build_llm() -> LLMClient:
    """Answering LLM for `/ask` and the benchmarks.

    `local` and `production` both use `LiteLLMClient`; the user picks
    the actual provider via `LLM_MODEL` (e.g. `ollama_chat/qwen2.5-coder:7b`
    locally, `anthropic/claude-3-5-sonnet-latest` in production).
    """
    if current_profile() == "mock":
        return MockLLMClient()
    return LiteLLMClient()


def build_summarizer_llm() -> LLMClient:
    """LLM used by the GraphRAG indexer to write community summaries.

    A separate constructor because `summarizer_model` defaults to the
    smaller 3B variant of qwen-coder; the answering LLM stays on 7B.
    """
    if current_profile() == "mock":
        return MockLLMClient(model="mock-summarizer-v1")
    settings = get_settings()
    return LiteLLMClient(model=settings.summarizer_model)


def build_dense(*, embedder: EmbeddingProvider, sqlite_path: Path) -> DenseRetriever:
    """Dense retriever per profile.

    `mock` and `local` build a `LocalDenseIndex` directly from the
    embeddings table in SQLite — no daemon required. `production`
    contacts the Go HNSW server via gRPC.
    """
    if current_profile() == "production":
        return HnswGrpcClient(
            expected_model=embedder.model,
            expected_dim=embedder.dim,
        )
    idx = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    store = EmbeddingsStore(sqlite_path)
    try:
        store.init_schema()
        for ids, mat in store.iter_vectors(model=embedder.model):
            idx.add(ids, mat)
    finally:
        store.close()
    return idx


def build_community(
    *,
    embedder: EmbeddingProvider,
    sqlite_path: Path,
    repo: str | None = None,
) -> CommunityRetriever:
    """In-process community retriever; empty when the index has no Phase 3 rows."""
    retr = LocalCommunityRetriever.from_sqlite(
        sqlite_path,
        model=embedder.model,
        dim=embedder.dim,
        repo=repo,
    )
    if len(retr) == 0:
        return empty_retriever(model=embedder.model, dim=embedder.dim)
    return retr


# ------------------------------------------------------------ retrieval root


def build_retrieval_service(
    *,
    sqlite_path: Path,
    repo: str | None = None,
) -> RetrievalService:
    """Wire a fully-formed `RetrievalService` for the active profile.

    Both `reposage ask` and `POST /ask` go through here; they always see
    the same wiring. Benchmarks construct their LLM separately because
    they own the temp index, but they reuse the same backend constructors.
    """
    settings = get_settings()
    embedder = build_embedder()
    sparse = BM25SparseRetriever.from_sqlite(sqlite_path, repo=repo)
    dense = build_dense(embedder=embedder, sqlite_path=sqlite_path)
    community = build_community(embedder=embedder, sqlite_path=sqlite_path, repo=repo)
    answer_cache = (
        AnswerCache(capacity=settings.answer_cache_size) if settings.answer_cache_enabled else None
    )
    return RetrievalService(
        sqlite_path=sqlite_path,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=build_reranker(),
        llm=build_llm(),
        community=community,
        answer_cache=answer_cache,
    )
