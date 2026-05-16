"""BM25 sparse retrieval over code tokens.

Phase 2 ships rank-bm25 (pure Python). Phase 5 swaps in Tantivy for ~10x
indexing throughput; the interface here is the migration boundary.
"""

from __future__ import annotations

from collections.abc import Sequence


class BM25Index:
    def __init__(self, index_dir: str) -> None:
        self.index_dir = index_dir

    def build(self, doc_ids: Sequence[str], texts: Sequence[str]) -> None:
        raise NotImplementedError

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        raise NotImplementedError
