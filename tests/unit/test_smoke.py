"""Sanity tests so CI has something green to publish from day 1."""

from __future__ import annotations

from fastapi.testclient import TestClient
from reposage import __version__
from reposage.api.main import create_app
from reposage.retrieval.hybrid import rrf_fuse


def test_version_exposed() -> None:
    assert __version__


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_rrf_fuse_orders_by_inverse_rank() -> None:
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]], k=60)
    # `b` appears at rank 2 + rank 1; `a` at rank 1 + rank 2 — same total.
    # `c` and `d` only appear once each.
    assert fused["a"] == fused["b"]
    assert fused["a"] > fused["c"]
    assert fused["a"] > fused["d"]
