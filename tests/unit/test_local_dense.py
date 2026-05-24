"""Unit tests for `LocalDenseIndex` (numpy linear scan)."""

from __future__ import annotations

import numpy as np
import pytest
from reposage.retrieval.local_dense import LocalDenseIndex


@pytest.fixture
def small_idx() -> LocalDenseIndex:
    idx = LocalDenseIndex(model="m", dim=4)
    vecs = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.99, 0.01, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    idx.add(["a", "b", "c", "d"], vecs)
    return idx


async def test_search_returns_nearest(small_idx: LocalDenseIndex) -> None:
    hits = await small_idx.search([1.0, 0.0, 0.0, 0.0], top_k=2)
    assert [h.chunk_id for h in hits] == ["a", "b"]
    # Distance of "a" to itself is ~0, "b" is small but positive.
    assert hits[0].score < hits[1].score


async def test_search_handles_zero_query(small_idx: LocalDenseIndex) -> None:
    assert await small_idx.search([0.0, 0.0, 0.0, 0.0], top_k=2) == []


async def test_search_empty_index() -> None:
    idx = LocalDenseIndex(model="m", dim=4)
    assert await idx.search([1.0, 0.0, 0.0, 0.0], top_k=5) == []


async def test_dim_mismatch_raises() -> None:
    idx = LocalDenseIndex(model="m", dim=4)
    with pytest.raises(ValueError):
        idx.add(["a"], np.zeros((1, 3), dtype=np.float32))


async def test_add_normalises_vectors() -> None:
    idx = LocalDenseIndex(model="m", dim=2)
    idx.add(["a"], np.array([[3.0, 4.0]], dtype=np.float32))
    # Distance of normalised (3,4) to itself is 0.
    hits = await idx.search([3.0, 4.0], top_k=1)
    assert hits[0].chunk_id == "a"
    assert pytest.approx(hits[0].score, abs=1e-5) == 0.0
