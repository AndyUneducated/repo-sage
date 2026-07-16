"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from reposage import __version__
from reposage.api.routes import ask, health, webhook
from reposage.config import get_settings
from reposage.observability.otel import setup_tracing


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.otel_enabled:
        setup_tracing(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="RepoSage",
        version=__version__,
        description="Repository-level code Q&A with dual-index retrieval.",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(ask.router)
    app.include_router(webhook.router)
    return app


app = create_app()
