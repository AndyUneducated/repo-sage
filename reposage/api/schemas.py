"""Request / response models for the public HTTP surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A single source-code citation grounding an LLM answer."""

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    chunk_id: str | None = None


class LatencyMs(BaseModel):
    """Sub-stage timings in milliseconds.

    Phase 6 may add `embed_ms` and `rerank_ms` once those are split out of
    the umbrella `retrieve` measurement.
    """

    embed_ms: int = 0
    retrieve_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    repo: str | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    route_hint: Literal["auto", "graph", "community", "hybrid"] = "auto"


class AskResponse(BaseModel):
    """Phase 2 answer shape.

    `graph_context` is reserved for Phase 3 GraphRAG community summaries.
    Phase 2 always sets it to ``None`` so the contract is forward-stable.
    """

    question: str
    answer: str
    citations: list[Citation]
    route: str
    grounded: bool = True
    latency_ms: LatencyMs = Field(default_factory=LatencyMs)
    graph_context: object | None = None
