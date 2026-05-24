"""High-level orchestrators that compose multiple subsystems.

`RetrievalService` is the single entry point CLI and HTTP both call into,
so neither path can drift away from the other.
"""

from reposage.services.retrieval_service import (
    AnswerResult,
    LatencyBreakdown,
    RetrievalService,
)

__all__ = ["AnswerResult", "LatencyBreakdown", "RetrievalService"]
