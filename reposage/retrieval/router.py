"""Query router — classifies a question into the right retrieval strategy.

The router is a small LLM call (or a regex-first heuristic when the question
clearly names a symbol). It picks one of:

* ``graph``     — deterministic adjacency lookup over the symbol graph
* ``community`` — module-level question, served by GraphRAG summaries
* ``hybrid``    — generic semantic question, served by HNSW + BM25 + reranker
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RouteName = Literal["graph", "community", "hybrid"]


@dataclass(slots=True, frozen=True)
class QueryRoute:
    name: RouteName
    confidence: float
    reason: str


class QueryRouter:
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    async def route(self, question: str) -> QueryRoute:
        # Phase 2: heuristic fast-path (regex on FQN-like tokens) + LLM fallback.
        raise NotImplementedError
