"""Unit tests for the `rrf_fuse` helper."""

from __future__ import annotations

import pytest
from reposage.retrieval.hybrid import rrf_fuse


def test_same_order_doubles_score() -> None:
    fused = rrf_fuse([["a", "b"], ["a", "b"]], k=60)
    # Score for 'a' = 2 * 1/(60+1) ; for 'b' = 2 * 1/(60+2).
    assert fused["a"] > fused["b"]
    assert pytest.approx(fused["a"], rel=1e-9) == 2.0 / 61.0


def test_disjoint_lists_keep_both() -> None:
    fused = rrf_fuse([["a"], ["b"]], k=60)
    assert set(fused.keys()) == {"a", "b"}
    assert pytest.approx(fused["a"], rel=1e-9) == 1.0 / 61.0
    assert pytest.approx(fused["b"], rel=1e-9) == 1.0 / 61.0


def test_different_orders_combine_correctly() -> None:
    """`a` is rank 1 in dense, rank 3 in sparse; `b` is the inverse."""
    fused = rrf_fuse([["a", "x", "b"], ["b", "x", "a"]], k=60)
    # Both 'a' and 'b' should score identically (1/61 + 1/63).
    assert pytest.approx(fused["a"], rel=1e-9) == fused["b"]
    # All three are within numerical noise — RRF rewards consistency
    # across rankings, but here `x` ranks 2 in both while `a`/`b` are
    # 1+3 in opposite branches; the totals are essentially identical.
    # Use a smaller k where rank differences dominate to validate.
    fused2 = rrf_fuse([["a", "x", "b"], ["b", "x", "a"]], k=1)
    # Now: a = 1/2 + 1/4 = 0.75 ; x = 2/3 ≈ 0.667 ; b = 0.75
    assert fused2["a"] > fused2["x"]
    assert fused2["b"] > fused2["x"]


def test_empty_rankings() -> None:
    assert rrf_fuse([], k=60) == {}
    assert rrf_fuse([[]], k=60) == {}


def test_k_parameter_changes_decay() -> None:
    # Smaller k → bigger contribution from rank 1.
    a_small = rrf_fuse([["a"]], k=1)["a"]
    a_big = rrf_fuse([["a"]], k=1000)["a"]
    assert a_small > a_big
