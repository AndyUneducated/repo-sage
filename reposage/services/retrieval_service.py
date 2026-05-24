"""End-to-end orchestration: question -> route -> retrieve -> answer -> ground.

Both the FastAPI `POST /ask` route and `reposage ask` go through here so
the two surfaces cannot diverge. The service is constructed once with all
backends already wired (embedder, dense, sparse, reranker, LLM, router).

The result shape mirrors the HTTP contract: an `answer`, a `citations`
list, the `route` chosen, a `latency_ms` breakdown for observability, and
a `graph_context` slot reserved for Phase 3 GraphRAG.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from reposage.indexer.embedder import EmbeddingProvider
from reposage.llm.grounding import (
    Citation,
    GroundingResult,
    strip_bad_citations,
    verify_grounding,
)
from reposage.llm.prompts import build_answer_messages
from reposage.retrieval.hybrid import HybridRetriever, RetrievedChunk
from reposage.retrieval.protocols import DenseRetriever, LLMClient, Reranker, SparseRetriever
from reposage.retrieval.router import QueryRoute, QueryRouter
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class LatencyBreakdown:
    embed_ms: int = 0
    retrieve_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0


@dataclass(slots=True)
class AnswerResult:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    route: str = "hybrid"
    latency: LatencyBreakdown = field(default_factory=LatencyBreakdown)
    grounded: bool = True
    graph_context: object | None = None  # Phase 3 GraphRAG slot


class RetrievalService:
    """Construct once at boot; reuse across requests."""

    def __init__(
        self,
        *,
        sqlite_path: Path,
        embedder: EmbeddingProvider,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        reranker: Reranker | None,
        llm: LLMClient,
        router: QueryRouter | None = None,
        top_k: int = 8,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.llm = llm
        self.top_k = top_k
        self.router = router or QueryRouter(llm=llm)
        self.hybrid = HybridRetriever(
            dense=dense,
            sparse=sparse,
            embedder=embedder,
            sqlite_path=sqlite_path,
            reranker=reranker,
        )
        self._embedder = embedder

    async def answer(
        self,
        question: str,
        *,
        repo: str | None = None,
        route_hint: str | None = None,
        top_k: int | None = None,
    ) -> AnswerResult:
        t0 = time.monotonic()
        decision = await self._route(question, route_hint=route_hint)

        if decision.name == "graph":
            assert decision.symbol is not None
            return self._answer_graph(
                question=question,
                decision=decision,
                t0=t0,
            )

        # community route is Phase 3; fall back to hybrid for now.
        if decision.name == "community":
            decision = QueryRoute(
                name="hybrid",
                confidence=decision.confidence,
                reason="community route deferred to Phase 3, falling back to hybrid",
                symbol=None,
            )

        return await self._answer_hybrid(
            question=question,
            decision=decision,
            t0=t0,
            top_k=top_k or self.top_k,
            repo=repo,
        )

    # --------------------------------------------------------------- routing

    async def _route(self, question: str, *, route_hint: str | None) -> QueryRoute:
        if route_hint in {"graph", "hybrid", "community"}:
            symbol = self.router.detect_symbol(question) if route_hint == "graph" else None
            return QueryRoute(
                name=route_hint,  # type: ignore[arg-type]
                confidence=1.0,
                reason="forced by route_hint",
                symbol=symbol,
            )
        return await self.router.route(question)

    # ----------------------------------------------------------- graph route

    def _answer_graph(self, *, question: str, decision: QueryRoute, t0: float) -> AnswerResult:
        """Walk the symbol graph with no LLM call.

        Phase 1's CLI did exactly this; we keep the behaviour bit-exact and
        only repackage the result into the new AnswerResult contract so the
        HTTP surface is consistent across routes.
        """
        symbol = decision.symbol or ""
        store = SQLiteSymbolGraphStore(self.sqlite_path)
        store.init_schema()
        try:
            nodes = store.find_nodes_by_suffix(symbol)
            lines: list[str] = []
            citations: list[Citation] = []
            for node in nodes:
                callers = store.callers_of(node.fqn)
                if not callers:
                    lines.append(f"- {node.fqn} has no recorded callers.")
                    continue
                lines.append(f"- {node.fqn}:")
                for e in callers:
                    lines.append(f"    - {e.src} at [{e.src_path}:{e.src_line}-{e.src_line}]")
                    citations.append(
                        Citation(path=e.src_path, start_line=e.src_line, end_line=e.src_line)
                    )
            answer = "\n".join(lines) if lines else f"No symbol matching {symbol!r} in the index."
        finally:
            store.close()
        elapsed = int((time.monotonic() - t0) * 1000)
        return AnswerResult(
            question=question,
            answer=answer,
            citations=citations,
            chunks=[],
            route="graph",
            latency=LatencyBreakdown(total_ms=elapsed),
            grounded=True,
            graph_context=None,
        )

    # ---------------------------------------------------------- hybrid route

    async def _answer_hybrid(
        self,
        *,
        question: str,
        decision: QueryRoute,
        t0: float,
        top_k: int,
        repo: str | None,
    ) -> AnswerResult:
        del decision  # captured in result.route below
        retrieve_t0 = time.monotonic()
        chunks = await self.hybrid.retrieve(question, repo=repo, top_k=top_k)
        retrieve_ms = int((time.monotonic() - retrieve_t0) * 1000)

        llm_t0 = time.monotonic()
        messages = build_answer_messages(question, chunks)
        raw_answer = await self.llm.complete(messages)
        llm_ms = int((time.monotonic() - llm_t0) * 1000)

        ground = verify_grounding(raw_answer, chunks)
        if not ground.valid:
            ground = await self._regenerate(question, chunks, ground)

        elapsed = int((time.monotonic() - t0) * 1000)
        return AnswerResult(
            question=question,
            answer=ground.answer,
            citations=ground.citations,
            chunks=list(chunks),
            route="hybrid",
            latency=LatencyBreakdown(
                retrieve_ms=retrieve_ms,
                llm_ms=llm_ms,
                total_ms=elapsed,
            ),
            grounded=ground.valid,
            graph_context=None,
        )

    async def _regenerate(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        bad: GroundingResult,
    ) -> GroundingResult:
        """One-shot regeneration with bad citations called out.

        DD-013: we do *not* loop. If the second attempt also fabricates,
        we return the answer with the bad citations stripped so callers
        always have something to show.
        """
        logger.warning(
            "grounding failed: %d bad citations on first attempt; regenerating",
            len(bad.dropped_citations),
        )
        # Build a new prompt that explicitly forbids the bad citations.
        forbidden = "\n".join(
            f"- {c.path}:{c.start_line}-{c.end_line}" for c in bad.dropped_citations
        )
        from reposage.retrieval.protocols import ChatMessage  # noqa: PLC0415

        messages = list(build_answer_messages(question, chunks))
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    "Your previous answer cited locations that are not in the "
                    "context. Rewrite the answer using only the chunks above; "
                    "do not reference these locations:\n"
                    f"{forbidden}\n"
                ),
            )
        )
        retry = await self.llm.complete(messages)
        verified = verify_grounding(retry, chunks)
        if verified.valid:
            return verified
        logger.warning(
            "grounding still failed after regeneration; stripping %d bad citations",
            len(verified.dropped_citations),
        )
        cleaned = strip_bad_citations(verified.answer, verified.dropped_citations)
        return GroundingResult(
            answer=cleaned,
            citations=verified.citations,
            dropped_citations=verified.dropped_citations,
            valid=False,
        )
