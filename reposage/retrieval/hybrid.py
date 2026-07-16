"""Hybrid retrieval: dense (HNSW) + sparse (BM25) fused with RRF.

Reciprocal Rank Fusion is preferred over weighted-sum (DD-006) because it
is score-scale invariant — we do not need to normalise BM25 scores against
cosine similarity.

The retriever loads a `chunks` row by `chunk_id` to attach `path`,
`start_line`, and `end_line` to each hit, since downstream stages
(reranker, citation grounding, LLM context formatting) all need them.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from reposage.indexer.embedder import EmbeddingProvider
from reposage.observability.otel import span
from reposage.retrieval.protocols import (
    DenseRetriever,
    Reranker,
    ScoredId,
    SparseRetriever,
)

if TYPE_CHECKING:
    from opentelemetry.trace import Span


@dataclass(slots=True, frozen=True)
class RetrievedChunk:
    chunk_id: str
    repo: str
    path: Path
    start_line: int
    end_line: int
    text: str
    symbol: str | None
    score: float
    source: Literal["hnsw", "bm25", "rrf", "rerank"]


def rrf_fuse(rankings: Iterable[Iterable[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion. ``rankings`` is an iterable of ranked id lists."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            out[doc_id] = out.get(doc_id, 0.0) + 1.0 / (k + rank)
    return out


class HybridRetriever:
    """Dense + sparse fan-out with RRF fusion and optional reranking.

    The constructor takes pre-built backends so the `RetrievalService`
    can construct them once at startup and reuse across requests. This
    keeps each request path allocation-free for the parts that matter.
    """

    def __init__(
        self,
        *,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        embedder: EmbeddingProvider,
        sqlite_path: Path,
        reranker: Reranker | None = None,
        top_k_per_branch: int = 50,
        rrf_k: int = 60,
        rerank_top_n: int = 20,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.embedder = embedder
        self.sqlite_path = sqlite_path
        self.reranker = reranker
        self.top_k_per_branch = top_k_per_branch
        self.rrf_k = rrf_k
        self.rerank_top_n = rerank_top_n

    async def retrieve(
        self,
        query: str,
        *,
        repo: str | None = None,
        top_k: int = 8,
        rerank: bool | None = None,
    ) -> list[RetrievedChunk]:
        with span("retrieval.hybrid", {"top_k": top_k}) as sp:
            return await self._retrieve(query, repo=repo, top_k=top_k, rerank=rerank, sp=sp)

    async def _retrieve(
        self,
        query: str,
        *,
        repo: str | None,
        top_k: int,
        rerank: bool | None,
        sp: Span | None,
    ) -> list[RetrievedChunk]:
        # Embed the query (single text → single (1, dim) row).
        query_vec = self.embedder.embed([query])[0].tolist()

        async def _dense() -> list[ScoredId]:
            with span("retrieval.dense", {"top_k_per_branch": self.top_k_per_branch}):
                return await self.dense.search(query_vec, top_k=self.top_k_per_branch)

        async def _sparse() -> list[ScoredId]:
            with span("retrieval.sparse", {"top_k_per_branch": self.top_k_per_branch}):
                return await self.sparse.search(query, top_k=self.top_k_per_branch)

        dense_task = asyncio.create_task(_dense())
        sparse_task = asyncio.create_task(_sparse())
        dense_hits, sparse_hits = await asyncio.gather(dense_task, sparse_task)
        if sp is not None:
            sp.set_attribute("retrieval.dense_hits", len(dense_hits))
            sp.set_attribute("retrieval.sparse_hits", len(sparse_hits))

        fused = rrf_fuse(
            [
                [h.chunk_id for h in dense_hits],
                [h.chunk_id for h in sparse_hits],
            ],
            k=self.rrf_k,
        )
        if not fused:
            return []

        # Sort by RRF score descending.
        order = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        rerank_n = (
            self.rerank_top_n
            if (rerank if rerank is not None else self.reranker is not None)
            else top_k
        )
        candidate_ids = [cid for cid, _ in order[:rerank_n]]
        chunks_by_id = self._fetch_chunks(candidate_ids, repo=repo)

        candidates: list[RetrievedChunk] = []
        for cid in candidate_ids:
            row = chunks_by_id.get(cid)
            if row is None:
                # Stale chunk_id (e.g. file deleted between index and serve).
                # Drop silently — the next request will see a fresh result set.
                continue
            candidates.append(
                RetrievedChunk(
                    chunk_id=cid,
                    repo=row["repo"],
                    path=Path(row["path"]),
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    text=row["text"],
                    symbol=row["symbol"],
                    score=fused[cid],
                    source="rrf",
                )
            )

        do_rerank = rerank if rerank is not None else (self.reranker is not None)
        if do_rerank and self.reranker is not None and candidates:
            with span("retrieval.rerank", {"n_candidates": len(candidates)}):
                scored = self.reranker.rerank(
                    query,
                    [(c.chunk_id, c.text) for c in candidates],
                    top_k=top_k,
                )
            score_by_id = {s.chunk_id: s.score for s in scored}
            candidates = [
                RetrievedChunk(
                    chunk_id=c.chunk_id,
                    repo=c.repo,
                    path=c.path,
                    start_line=c.start_line,
                    end_line=c.end_line,
                    text=c.text,
                    symbol=c.symbol,
                    score=score_by_id[c.chunk_id],
                    source="rerank",
                )
                for c in candidates
                if c.chunk_id in score_by_id
            ]
            candidates.sort(key=lambda c: c.score, reverse=True)

        result = candidates[:top_k]
        if sp is not None:
            sp.set_attribute("retrieval.n_results", len(result))
        return result

    def _fetch_chunks(
        self, chunk_ids: Sequence[str], *, repo: str | None
    ) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" * len(chunk_ids))
            sql = (
                f"SELECT chunk_id, repo, path, start_line, end_line, symbol, text "
                f"FROM chunks WHERE chunk_id IN ({placeholders})"
            )
            params: list[object] = list(chunk_ids)
            if repo is not None:
                sql += " AND repo = ?"
                params.append(repo)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return {r["chunk_id"]: r for r in rows}
