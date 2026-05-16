"""Liveness / readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    # Phase 2: probe SQLite, HNSW gRPC, and BM25 directories.
    return {"status": "ready"}
