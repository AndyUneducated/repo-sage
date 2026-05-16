"""Request / response models for the public HTTP surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A single source-code citation grounding an LLM answer."""

    repo: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    symbol: str | None = None
    score: float | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)
    repo: str | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    route_hint: Literal["auto", "graph", "community", "hybrid"] = "auto"


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    route: str
    latency_ms: int
