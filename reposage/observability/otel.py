"""OpenTelemetry bootstrap + a tiny span helper.

Spans are emitted around: indexing pipeline stages, query router decisions,
each retrieval branch (HNSW / BM25 / graph), and LLM completions. The
EvalGate sister project consumes these traces for offline RAG quality
regression tests.

Two-tier design (DD: keep instrumentation free of setup cost):

* :func:`span` is the instrumentation surface used everywhere in the
  codebase. It never fails and never requires a provider — without
  :func:`setup_tracing` the underlying tracer returns non-recording spans,
  so the overhead is a couple of context-var writes.
* :func:`setup_tracing` wires the OTLP exporter. It is called only when
  ``settings.otel_enabled`` is set, so default runs (CLI, tests, a fresh
  clone) never open a socket to a collector that isn't there.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer

logger = logging.getLogger(__name__)
_INITIALISED = False

# Attribute values OpenTelemetry accepts on a span (scalar subset we use).
AttributeValue = str | bool | int | float


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


@contextmanager
def span(
    name: str,
    attributes: Mapping[str, AttributeValue] | None = None,
) -> Iterator[Span | None]:
    """Start a span as the current context and yield it (or ``None``).

    Safe to call anywhere:

    * If the OpenTelemetry API is unavailable, yields ``None`` and does
      nothing (the caller must guard ``if sp is not None`` before touching
      the span — mypy enforces this).
    * If no ``TracerProvider`` was installed (the default), the tracer hands
      back a non-recording span, so ``start_as_current_span`` /
      ``set_attribute`` are cheap no-ops.

    Attributes known up front should be passed via ``attributes``; ones
    computed inside the block can be set on the yielded span with a guard.
    """
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover -- opentelemetry-api is a core dep
        yield None
        return

    tracer = trace.get_tracer("reposage")
    with tracer.start_as_current_span(name) as current:
        if attributes:
            for key, value in attributes.items():
                current.set_attribute(key, value)
        yield current
