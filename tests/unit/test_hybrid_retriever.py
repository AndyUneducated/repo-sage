"""HybridRetriever orchestration contract.

Pinned guarantees:

1. Dense + sparse retrievers are *both* called per query (parallel fan-out).
2. Each branch is asked for `top_k_per_branch` (default 50), regardless of
   the final `top_k` requested.
3. RRF fusion preserves the union of dense + sparse hits, sorted by RRF
   score descending.
4. Reranker, if present, runs on the top-N RRF candidates and trims to the
   final `top_k`.
5. With ``rerank=False`` the reranker is bypassed entirely; with no
   reranker configured at all, the truncation falls back to top-k slicing.
6. The optional `repo` filter narrows the SQLite chunk fetch.

These wiring tests use stub dense/sparse/reranker so the contract is
exercised without sentence-transformers, BM25, or HNSW.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.retrieval.hybrid import HybridRetriever
from reposage.retrieval.protocols import ScoredId


class StubDense:
    def __init__(self, hits: Sequence[ScoredId]) -> None:
        self._hits = list(hits)
        self.calls: list[tuple[Sequence[float], int]] = []

    @property
    def model(self) -> str:
        return "stub-dense"

    @property
    def dim(self) -> int:
        return 768

    async def search(
        self, query_vector: Sequence[float], top_k: int = 50, ef_search: int | None = None
    ) -> list[ScoredId]:
        self.calls.append((list(query_vector), top_k))
        return list(self._hits[:top_k])

    async def healthcheck(self) -> bool:
        return True


class StubSparse:
    def __init__(self, hits: Sequence[ScoredId]) -> None:
        self._hits = list(hits)
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, top_k: int = 50) -> list[ScoredId]:
        self.calls.append((query, top_k))
        return list(self._hits[:top_k])


class StubReranker:
    """Reverses the input order so the test can detect that it ran."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[str, str]], int]] = []

    @property
    def model(self) -> str:
        return "stub-reranker"

    def rerank(
        self, query: str, candidates: Sequence[tuple[str, str]], top_k: int = 8
    ) -> list[ScoredId]:
        self.calls.append((query, list(candidates), top_k))
        # Higher score for later items so the order reverses; truncate to top_k.
        scored = [ScoredId(chunk_id=cid, score=float(i)) for i, (cid, _) in enumerate(candidates)]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


@pytest.fixture
def chunk_db(tmp_path: Path) -> Path:
    """Build a SQLite DB with hand-rolled chunks; no indexing pipeline."""
    db = tmp_path / "index.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chunks(
          chunk_id      TEXT PRIMARY KEY,
          repo          TEXT NOT NULL,
          path          TEXT NOT NULL,
          language      TEXT NOT NULL,
          start_line    INTEGER NOT NULL,
          end_line      INTEGER NOT NULL,
          symbol        TEXT,
          parent_symbol TEXT,
          text          TEXT NOT NULL,
          file_sha      TEXT NOT NULL,
          created_at    INTEGER NOT NULL
        );
        """
    )
    rows = []
    # 6 chunks across two repos so we can test repo filtering and top-k cuts.
    for i, (repo, path, sym) in enumerate(
        [
            ("alpha", "a.py", "A"),
            ("alpha", "b.py", "B"),
            ("alpha", "c.py", "C"),
            ("alpha", "d.py", "D"),
            ("beta", "x.py", "X"),
            ("beta", "y.py", "Y"),
        ]
    ):
        rows.append(
            (
                f"c{i}",
                repo,
                path,
                "python",
                1,
                10,
                sym,
                None,
                f"text for chunk {i} in {path} symbol {sym}",
                "deadbeef",
                0,
            )
        )
    conn.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _make(
    db: Path,
    *,
    dense_hits: Sequence[str],
    sparse_hits: Sequence[str],
    reranker: object | None = None,
    rerank_top_n: int = 20,
) -> tuple[HybridRetriever, StubDense, StubSparse]:
    dense = StubDense([ScoredId(c, 1.0) for c in dense_hits])
    sparse = StubSparse([ScoredId(c, 1.0) for c in sparse_hits])
    embedder = HashEmbedder()
    retriever = HybridRetriever(
        dense=dense,
        sparse=sparse,
        embedder=embedder,
        sqlite_path=db,
        reranker=reranker,  # type: ignore[arg-type]
        rerank_top_n=rerank_top_n,
    )
    return retriever, dense, sparse


@pytest.mark.asyncio
async def test_dense_and_sparse_both_called_with_top_k_per_branch(chunk_db: Path) -> None:
    retriever, dense, sparse = _make(
        chunk_db,
        dense_hits=["c0", "c1"],
        sparse_hits=["c2", "c3"],
    )
    await retriever.retrieve("query about A", top_k=4)
    assert len(dense.calls) == 1
    assert len(sparse.calls) == 1
    # Both branches must request top_k_per_branch (default 50), NOT the
    # caller's top_k. Otherwise RRF starves on a tiny union.
    assert dense.calls[0][1] == retriever.top_k_per_branch == 50
    assert sparse.calls[0][1] == retriever.top_k_per_branch == 50


@pytest.mark.asyncio
async def test_rrf_union_keeps_unique_hits(chunk_db: Path) -> None:
    """Disjoint dense/sparse → result contains the union (subject to top_k)."""
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c1"],
        sparse_hits=["c2", "c3"],
    )
    out = await retriever.retrieve("q", top_k=10)
    ids = [c.chunk_id for c in out]
    assert set(ids) == {"c0", "c1", "c2", "c3"}


@pytest.mark.asyncio
async def test_top_k_truncation_without_reranker(chunk_db: Path) -> None:
    """No reranker → final list is sliced to top_k by RRF score."""
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c1", "c2", "c3"],
        sparse_hits=["c0", "c1", "c2", "c3"],
        reranker=None,
    )
    out = await retriever.retrieve("q", top_k=2)
    assert len(out) == 2
    # The RRF source label travels with each retrieved chunk so downstream
    # callers know nothing has reranked yet.
    assert all(c.source == "rrf" for c in out)


@pytest.mark.asyncio
async def test_reranker_runs_and_caps_at_top_k(chunk_db: Path) -> None:
    rer = StubReranker()
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c1", "c2"],
        sparse_hits=["c3"],
        reranker=rer,
    )
    out = await retriever.retrieve("q", top_k=2)
    assert len(rer.calls) == 1, "reranker must run exactly once"
    # StubReranker reverses order so the LAST candidate becomes the head.
    assert len(out) == 2
    assert all(c.source == "rerank" for c in out)
    # `top_k` must be propagated to reranker so it can short-circuit.
    assert rer.calls[0][2] == 2


@pytest.mark.asyncio
async def test_rerank_false_skips_reranker_even_when_present(chunk_db: Path) -> None:
    rer = StubReranker()
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c1"],
        sparse_hits=["c2"],
        reranker=rer,
    )
    out = await retriever.retrieve("q", top_k=3, rerank=False)
    assert rer.calls == [], "rerank=False must bypass the reranker"
    assert all(c.source == "rrf" for c in out)


@pytest.mark.asyncio
async def test_repo_filter_excludes_other_repos(chunk_db: Path) -> None:
    """Hits from other repos are dropped during the SQLite chunk fetch."""
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c4"],  # c0 is alpha, c4 is beta
        sparse_hits=["c5"],  # c5 is beta
    )
    out = await retriever.retrieve("q", repo="alpha", top_k=10)
    repos = {c.repo for c in out}
    assert repos == {"alpha"}
    assert all(c.chunk_id == "c0" for c in out)


@pytest.mark.asyncio
async def test_empty_branches_return_empty(chunk_db: Path) -> None:
    """Both branches empty → no exception, empty list, no DB read."""
    retriever, _, _ = _make(chunk_db, dense_hits=[], sparse_hits=[])
    out = await retriever.retrieve("q", top_k=8)
    assert out == []


@pytest.mark.asyncio
async def test_stale_chunk_ids_silently_dropped(chunk_db: Path) -> None:
    """Hits referring to ids no longer in `chunks` must not crash retrieval."""
    retriever, _, _ = _make(
        chunk_db,
        dense_hits=["c0", "c-stale"],
        sparse_hits=["c1"],
    )
    out = await retriever.retrieve("q", top_k=10)
    ids = {c.chunk_id for c in out}
    assert "c-stale" not in ids
    assert {"c0", "c1"}.issubset(ids)


def test_rrf_score_decreases_with_rank() -> None:
    """Sanity: a doc at rank 1 gets a strictly higher RRF score than at rank 2."""
    from reposage.retrieval.hybrid import rrf_fuse  # noqa: PLC0415

    fused = rrf_fuse([["a", "b", "c"]], k=60)
    assert fused["a"] > fused["b"] > fused["c"]


def test_rrf_two_branches_combine_additively() -> None:
    """A doc in both rankings beats a doc in only one (same rank)."""
    from reposage.retrieval.hybrid import rrf_fuse  # noqa: PLC0415

    fused = rrf_fuse([["a", "x"], ["a", "y"]], k=60)
    assert fused["a"] > fused["x"]
    assert fused["a"] > fused["y"]


@pytest.mark.asyncio
async def test_retriever_uses_embedder_for_dense_query(chunk_db: Path) -> None:
    """The dense branch receives the embedded query, not the raw text."""
    retriever, dense, _ = _make(
        chunk_db,
        dense_hits=["c0"],
        sparse_hits=[],
    )
    await retriever.retrieve("hello world", top_k=1)
    assert dense.calls, "dense.search was never called"
    vec = dense.calls[0][0]
    # HashEmbedder produces a 768-d unit-norm vector deterministically.
    assert len(vec) == 768
    np.testing.assert_allclose(np.linalg.norm(vec), 1.0, atol=1e-5)
