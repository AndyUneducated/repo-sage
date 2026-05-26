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


class Outcome(BaseModel):
    """Which route produced the answer + whether a richer route degraded.

    Replaces the legacy `route: str` field. `route` is always the
    terminal route that produced the answer; `degraded_from` is filled
    only when a richer route (currently just `community`) gave up
    half-way and the service re-ran on hybrid.
    """

    route: Literal["graph", "community", "hybrid"]
    degraded_from: Literal["community"] | None = None
    degrade_reason: str | None = None


class CommunityHit(BaseModel):
    """One community returned for the GraphRAG community route."""

    community_id: int
    level: int
    title: str | None = None
    summary: str | None = None
    score: float = 0.0


class CommunityContext(BaseModel):
    """`AskResponse.graph_context` payload for the community route.

    Phase 2 always sets `AskResponse.graph_context` to `None`; Phase 3
    fills it on the community route. The field stays optional so older
    clients are unaffected.
    """

    communities: list[CommunityHit]


class AskResponse(BaseModel):
    """Phase 2 / 3 answer shape.

    `outcome` records the terminal route and any degradation. Earlier
    schemas used a flat `route: str` (with a concatenated
    "community-degraded-to-hybrid" string); the structured field makes
    the degradation machine-readable for dashboards and removes the
    ambiguity in the enum.

    `graph_context` is `None` on the graph and hybrid routes; on the
    community route (and on community-degraded-to-hybrid answers) it
    carries the list of communities that drove the answer so a UI can
    render the module-level context alongside the file citations.
    """

    question: str
    answer: str
    citations: list[Citation]
    outcome: Outcome
    grounded: bool = True
    latency_ms: LatencyMs = Field(default_factory=LatencyMs)
    graph_context: CommunityContext | None = None
