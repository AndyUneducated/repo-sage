"""OpenTelemetry bootstrap.

Spans are emitted around: indexing pipeline stages, query router decisions,
each retrieval branch (HNSW / BM25 / graph), and LLM completions. The
EvalGate sister project consumes these traces for offline RAG quality
regression tests.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)
_INITIALISED = False


def setup_tracing(service_name: str, endpoint: str) -> None:
    global _INITIALISED
    if _INITIALISED:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover -- dev-only fallback
        logger.warning("opentelemetry packages missing; tracing disabled")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    _INITIALISED = True


def get_tracer(name: str = "reposage") -> Tracer:
    from opentelemetry import trace

    return trace.get_tracer(name)
