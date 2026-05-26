"""FastAPI dependency wiring — a thin shell over `reposage.composition`.

Tests still override `get_retrieval_service` via `app.dependency_overrides`
when they want to inject a fully-fake `RetrievalService`. Everything else
flows through `reposage.composition.build_retrieval_service`, which is the
sole reader of `REPOSAGE_PROFILE`.
"""

from __future__ import annotations

from functools import lru_cache

from reposage.composition import build_retrieval_service
from reposage.config import get_settings
from reposage.services.retrieval_service import RetrievalService


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    return build_retrieval_service(sqlite_path=settings.sqlite_path)


def reset_retrieval_service() -> None:
    """Drop the cached service. Tests call this to force a rebuild."""
    get_retrieval_service.cache_clear()
