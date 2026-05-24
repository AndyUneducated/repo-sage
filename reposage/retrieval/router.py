"""Query router — classifies a question into the right retrieval strategy.

Phase 1 introduced the deterministic ``graph`` fast-path: if the question
contains a dotted name like ``User.login``, a snake_case identifier, or a
parenthesised call, we skip the LLM and walk the symbol graph directly.

Phase 2 adds an LLM-backed fallback for the remaining questions. The LLM
produces a single line of JSON; we parse it defensively (questions that
trip a parse error fall back to ``hybrid``, which is the safety net).

Three routes:

* ``graph``     — deterministic adjacency lookup, no LLM in the answer.
* ``community`` — module-level question (Phase 3 GraphRAG); Phase 2 falls
  back to ``hybrid`` after marking the route, so the contract is stable.
* ``hybrid``    — generic semantic search via HNSW + BM25 + RRF + reranker.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from reposage.llm.prompts import build_router_messages
from reposage.retrieval.protocols import LLMClient

logger = logging.getLogger(__name__)

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
    def __init__(self, model: str | None = None, *, llm: LLMClient | None = None) -> None:
        self.model = model
        self.llm = llm

    # ------------------------------------------------ symbol detection

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

    # ----------------------------------------------------- sync route

    def route_sync(self, question: str) -> QueryRoute:
        """Synchronous heuristic route; never calls the LLM.

        Returns ``graph`` when a symbol is detected, otherwise ``hybrid`` as
        a safety net. The async :meth:`route` adds the LLM fallback for the
        latter and is what the production ``RetrievalService`` calls.
        """
        symbol = self.detect_symbol(question)
        if symbol is not None:
            return QueryRoute(
                name="graph",
                confidence=1.0,
                reason="symbolic",
                symbol=symbol,
            )
        return QueryRoute(
            name="hybrid",
            confidence=0.5,
            reason="no symbol detected; defaulting to hybrid (LLM fallback off)",
        )

    # ---------------------------------------------------- async route

    async def route(self, question: str) -> QueryRoute:
        symbol = self.detect_symbol(question)
        if symbol is not None:
            return QueryRoute(
                name="graph",
                confidence=1.0,
                reason="symbolic",
                symbol=symbol,
            )
        if self.llm is None:
            return self.route_sync(question)
        try:
            return await self._llm_route(question)
        except Exception as exc:
            # The router should never block answering. If the LLM call
            # fails (auth, network, parse error), default to hybrid and
            # log so the operator can investigate.
            logger.warning("router LLM fallback failed: %r", exc)
            return QueryRoute(
                name="hybrid",
                confidence=0.4,
                reason=f"router LLM unavailable ({type(exc).__name__}); hybrid",
            )

    async def _llm_route(self, question: str) -> QueryRoute:
        assert self.llm is not None
        raw = await self.llm.complete(list(build_router_messages(question)))
        # The model is instructed to return one JSON line. Strip any code
        # fences just in case.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.split("\n", 1)[-1]
        # Attempt to find the first {...} block in the response.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError(f"router output not JSON: {raw!r}")
        decoded = json.loads(cleaned[start : end + 1])
        name = decoded.get("route", "hybrid")
        if name not in {"graph", "community", "hybrid"}:
            name = "hybrid"
        confidence = float(decoded.get("confidence", 0.5))
        reason = str(decoded.get("reason", ""))
        return QueryRoute(name=name, confidence=confidence, reason=reason or "llm")
