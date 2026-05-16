"""Hybrid retriever: HNSW + BM25 fused with Reciprocal Rank Fusion.

RRF is preferred over weighted-sum because it is score-scale invariant — we
do not need to normalise BM25 scores against cosine similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class RetrievedChunk:
    repo: str
    path: Path
    start_line: int
    end_line: int
    text: str
    score: float
    source: str  # "hnsw" | "bm25" | "rrf" | "rerank"


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion. `rankings` is a list of ranked id lists."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            out[doc_id] = out.get(doc_id, 0.0) + 1.0 / (k + rank)
    return out


class HybridRetriever:
    def __init__(self, top_k_per_branch: int = 50, rrf_k: int = 60) -> None:
        self.top_k_per_branch = top_k_per_branch
        self.rrf_k = rrf_k

    async def retrieve(
        self,
        repo: str,
        query: str,
        top_k: int = 8,
        rerank: bool = True,
    ) -> list[RetrievedChunk]:
        # Phase 2: query embedder → HNSW client → BM25 → RRF → optional reranker.
        raise NotImplementedError
