"""Unit + integration tests for the Phase 9 versioned answer cache (DD-046)."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.bm25 import BM25SparseRetriever
from reposage.retrieval.local_dense import LocalDenseIndex
from reposage.retrieval.protocols import ChatMessage
from reposage.retrieval.reranker import MockReranker
from reposage.services.answer_cache import AnswerCache
from reposage.services.retrieval_service import (
    AnswerResult,
    LatencyBreakdown,
    RetrievalService,
)
from reposage.storage.embeddings_store import EmbeddingsStore

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


def test_make_key_is_stable_and_input_sensitive() -> None:
    base = dict(
        repo="demo",
        repo_version="sha:1",
        question="how does login work?",
        route_hint="hybrid",
        top_k=8,
        model="mock",
    )
    k = AnswerCache.make_key(**base)  # type: ignore[arg-type]
    assert k == AnswerCache.make_key(**base)  # type: ignore[arg-type]
    # whitespace-insensitive on the question
    assert k == AnswerCache.make_key(**{**base, "question": "  how does login work?  "})  # type: ignore[arg-type]
    # every other field flips the key
    for field, val in [
        ("repo", "other"),
        ("repo_version", "sha:2"),
        ("route_hint", "graph"),
        ("top_k", 4),
        ("model", "gpt"),
    ]:
        assert AnswerCache.make_key(**{**base, field: val}) != k  # type: ignore[arg-type]


def _result(question: str = "q") -> AnswerResult:
    return AnswerResult(question=question, answer="a", grounded=True)


def test_lru_eviction_and_hit_miss_counts() -> None:
    cache = AnswerCache(capacity=2)
    cache.put("k1", _result())
    cache.put("k2", _result())
    assert cache.get("k1") is not None  # touch k1 → k2 is now LRU
    cache.put("k3", _result())  # evicts k2
    assert cache.get("k2") is None
    assert "k1" in cache and "k3" in cache
    assert len(cache) == 2
    assert cache.hits == 1  # get("k1")
    assert cache.misses == 1  # get("k2") after eviction


def test_get_returns_copy_with_reset_latency() -> None:
    cache = AnswerCache(capacity=4)
    original = AnswerResult(question="q", answer="a", latency=LatencyBreakdown(total_ms=999))
    cache.put("k", original)
    hit = cache.get("k")
    assert hit is not None
    assert hit is not original  # copy, so caller mutation can't corrupt the entry
    assert hit.latency.total_ms == 0


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        AnswerCache(capacity=0)


class CountingLLM:
    def __init__(self, reply: str, model_name: str = "counting-llm") -> None:
        self._reply = reply
        self._model = model_name
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls += 1
        return self._reply


@pytest.mark.asyncio
async def test_service_cache_hit_skips_second_llm_call(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)

    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()

    llm = CountingLLM("Sessions are handled in the auth module.")  # no citation → grounded
    cache = AnswerCache(capacity=8)
    service = RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=llm,
        answer_cache=cache,
    )

    kwargs = dict(repo="tiny", route_hint="hybrid", top_k=4)
    first = await service.answer("How does Session.open work?", **kwargs)  # type: ignore[arg-type]
    second = await service.answer("How does Session.open work?", **kwargs)  # type: ignore[arg-type]

    assert llm.calls == 1, "second identical question must be served from cache"
    assert first.answer == second.answer
    assert cache.hits == 1 and len(cache) == 1


@pytest.mark.asyncio
async def test_service_without_cache_is_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)

    sparse = BM25SparseRetriever.from_sqlite(db, repo="tiny")
    dense = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    es = EmbeddingsStore(db)
    es.init_schema()
    for ids, mat in es.iter_vectors(model=embedder.model):
        dense.add(ids, mat)
    es.close()

    llm = CountingLLM("Sessions are handled in the auth module.")
    service = RetrievalService(
        sqlite_path=db,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=MockReranker(),
        llm=llm,
    )  # no answer_cache → cold every time
    kwargs = dict(repo="tiny", route_hint="hybrid", top_k=4)
    await service.answer("How does Session.open work?", **kwargs)  # type: ignore[arg-type]
    await service.answer("How does Session.open work?", **kwargs)  # type: ignore[arg-type]
    assert llm.calls == 2
