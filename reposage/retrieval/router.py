"""Query router — classifies a question into the right retrieval strategy.

The router is a small LLM call (or a regex-first heuristic when the question
clearly names a symbol). It picks one of:

* ``graph``     — deterministic adjacency lookup over the symbol graph
* ``community`` — module-level question, served by GraphRAG summaries
* ``hybrid``    — generic semantic question, served by HNSW + BM25 + reranker

Phase 1 only implements the deterministic ``graph`` fast-path. Other routes
raise ``NotImplementedError`` and Phase 2 wires them up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RouteName = Literal["graph", "community", "hybrid"]

# Tier 1: dotted name (high confidence — `User.login`, `pkg.mod.Foo.bar`).
_DOTTED_RE = re.compile(r"\b([A-Za-z_][\w]*\.[A-Za-z_][\w.]*)\b")
# Tier 2: identifier followed immediately by `(` — clearly a call.
_CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
# Tier 3: snake_case identifier with at least one underscore. Filters out
# common prose words like "session" or "user" while still catching things
# like `require_auth`, `make_session`, `issue_many`.
_SNAKE_RE = re.compile(r"\b([a-z_][a-z0-9_]*_[a-z0-9_]+)\b")


@dataclass(slots=True, frozen=True)
class QueryRoute:
    name: RouteName
    confidence: float
    reason: str
    symbol: str | None = None  # the dotted name extracted, when route == 'graph'


class QueryRouter:
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def detect_symbol(self, question: str) -> str | None:
        """Pull a symbol-like token out of ``question``.

        Tier 1 (dotted) is preferred. Tier 2 (call-shaped) catches things
        like ``require_auth(user)``. Tier 3 (snake_case) catches
        ``where is require_auth called?`` without parentheses.
        """
        m = _DOTTED_RE.search(question)
        if m:
            return m.group(1)
        m = _CALL_RE.search(question)
        if m:
            return m.group(1)
        m = _SNAKE_RE.search(question)
        if m:
            return m.group(1)
        return None

    def route_sync(self, question: str) -> QueryRoute:
        """Synchronous route used by Phase 1 CLI.

        Phase 2 will introduce an async ``route()`` that may issue a small
        LLM call when the heuristic is uncertain.
        """
        symbol = self.detect_symbol(question)
        if symbol is not None:
            return QueryRoute(
                name="graph",
                confidence=1.0,
                reason="symbolic",
                symbol=symbol,
            )
        # Future routes — Phase 2 will replace these branches.
        raise NotImplementedError(
            "Non-graph routes (hybrid / community) are not yet implemented; "
            "Phase 2 will wire up the LLM-backed router."
        )

    async def route(self, question: str) -> QueryRoute:
        # Phase 1: identical behaviour to ``route_sync``. Phase 2 may add LLM
        # fallback for non-symbolic questions.
        return self.route_sync(question)
