"""End-to-end orchestration: question -> route -> retrieve -> answer -> ground.

Both the FastAPI `POST /ask` route and `reposage ask` go through here so
the two surfaces cannot diverge. The service is constructed once with all
backends already wired (embedder, dense, sparse, reranker, LLM, router).

The result shape mirrors the HTTP contract: an `answer`, a `citations`
list, a `RouteOutcome` (which route ran + whether it degraded), a
`latency_ms` breakdown for observability, and a `graph_context` slot
filled by the Phase 3 community route.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from reposage.indexer.embedder import EmbeddingProvider
from reposage.llm.grounding import (
    Citation,
    GroundingResult,
    strip_bad_citations,
    verify_grounding,
)
from reposage.llm.prompts import build_answer_messages, build_community_answer_messages
from reposage.retrieval.hybrid import HybridRetriever, RetrievedChunk
from reposage.retrieval.protocols import (
    CommunityRetriever,
    DenseRetriever,
    LLMClient,
    Reranker,
    ScoredCommunity,
    SparseRetriever,
)
from reposage.retrieval.router import QueryRoute, QueryRouter
from reposage.storage.community_store import CommunityStore
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

logger = logging.getLogger(__name__)

RouteName = Literal["graph", "community", "hybrid"]


@dataclass(slots=True, frozen=True)
class LatencyBreakdown:
    embed_ms: int = 0
    retrieve_ms: int = 0
    rerank_ms: int = 0
    llm_ms: int = 0
    total_ms: int = 0


@dataclass(slots=True, frozen=True)
class RouteOutcome:
    """What actually ran, including whether a richer route degraded.

    `route` is the path the answer was finally produced from. When the
    community route gives up half-way (no retriever, no hits, no chunks
    for the surfaced communities), the dispatcher re-runs the question
    on hybrid and tags the outcome with `degraded_from="community"` so
    dashboards / log lines can tell apart "the router never picked
    community" from "community was tried and failed".
    """

    route: RouteName
    degraded_from: Literal["community"] | None = None
    degrade_reason: str | None = None


@dataclass(slots=True, frozen=True)
class CommunityContextItem:
    """Lightweight value shipped back inside `AnswerResult.graph_context`.

    Kept dataclass-shaped (not Pydantic) so the service layer stays
    framework-agnostic; the FastAPI route converts to the
    `CommunityContext` schema on the way out.
    """

    community_id: int
    level: int
    title: str | None
    summary: str | None
    score: float


@dataclass(slots=True)
class AnswerResult:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    outcome: RouteOutcome = field(default_factory=lambda: RouteOutcome(route="hybrid"))
    latency: LatencyBreakdown = field(default_factory=LatencyBreakdown)
    grounded: bool = True
    graph_context: list[CommunityContextItem] | None = None  # Phase 3 GraphRAG

    # Read-only convenience: most callers only care about the leaf route.
    # Existing log lines and the CLI display use `result.route`; surfacing
    # it as a property keeps that ergonomic without re-introducing a
    # second source of truth.
    @property
    def route(self) -> RouteName:
        return self.outcome.route


@dataclass(slots=True, frozen=True)
class _Degrade:
    """Sentinel returned by `_run_community` when it can't produce an answer.

    Carries the reason so the dispatcher can stamp it into the resulting
    `RouteOutcome.degrade_reason`. Using a sentinel (vs `None`) lets us
    return community context as a side-channel even when degrading, so
    a community-degraded-to-hybrid answer can still display "we found
    these communities but couldn't ground there".
    """

    reason: str
    community_context: list[CommunityContextItem] | None = None
    embed_ms: int = 0


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
        community: CommunityRetriever | None = None,
        top_k: int = 8,
        community_top_k: int = 5,
        community_chunks_per_hit: int = 4,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.llm = llm
        self.top_k = top_k
        self.community_top_k = community_top_k
        self.community_chunks_per_hit = community_chunks_per_hit
        self.router = router or QueryRouter(llm=llm)
        self.hybrid = HybridRetriever(
            dense=dense,
            sparse=sparse,
            embedder=embedder,
            sqlite_path=sqlite_path,
            reranker=reranker,
        )
        self._embedder = embedder
        # Phase 3: optional. When None the community route degrades to a
        # hybrid answer, mirroring the Phase 2 behaviour.
        self.community = community

    async def answer(
        self,
        question: str,
        *,
        repo: str | None = None,
        route_hint: str | None = None,
        top_k: int | None = None,
    ) -> AnswerResult:
        """Single dispatcher: route → linear `_run_*` → optional degrade."""
        t0 = time.monotonic()
        decision = await self._route(question, route_hint=route_hint)
        effective_top_k = top_k or self.top_k

        if decision.name == "graph":
            assert decision.symbol is not None
            return self._run_graph(question=question, decision=decision, t0=t0)

        if decision.name == "community":
            outcome_or_degrade = await self._run_community(
                question=question, t0=t0, top_k=effective_top_k, repo=repo
            )
            if isinstance(outcome_or_degrade, AnswerResult):
                return outcome_or_degrade
            logger.warning("community route degrading to hybrid: %s", outcome_or_degrade.reason)
            return await self._run_hybrid(
                question=question,
                t0=t0,
                top_k=effective_top_k,
                repo=repo,
                outcome=RouteOutcome(
                    route="hybrid",
                    degraded_from="community",
                    degrade_reason=outcome_or_degrade.reason,
                ),
                embed_ms=outcome_or_degrade.embed_ms,
                community_context=outcome_or_degrade.community_context,
            )

        return await self._run_hybrid(
            question=question,
            t0=t0,
            top_k=effective_top_k,
            repo=repo,
            outcome=RouteOutcome(route="hybrid"),
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

    def _run_graph(self, *, question: str, decision: QueryRoute, t0: float) -> AnswerResult:
        """Walk the symbol graph with no LLM call.

        Phase 1's CLI did exactly this; we keep the behaviour bit-exact
        and only repackage the result into the new `AnswerResult`
        contract so the HTTP surface is consistent across routes.
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
            outcome=RouteOutcome(route="graph"),
            latency=LatencyBreakdown(total_ms=elapsed),
            grounded=True,
            graph_context=None,
        )

    # ---------------------------------------------------------- hybrid route

    async def _run_hybrid(
        self,
        *,
        question: str,
        t0: float,
        top_k: int,
        repo: str | None,
        outcome: RouteOutcome,
        embed_ms: int = 0,
        community_context: list[CommunityContextItem] | None = None,
    ) -> AnswerResult:
        """Phase 2 hybrid retrieval + grounded answer.

        `outcome` is supplied by the caller so the same code path serves
        both "router picked hybrid" and "community degraded to hybrid".
        `embed_ms` / `community_context` come from a partial community
        run when degrading.
        """
        retrieve_t0 = time.monotonic()
        chunks = await self.hybrid.retrieve(question, repo=repo, top_k=top_k)
        retrieve_ms = int((time.monotonic() - retrieve_t0) * 1000)

        llm_t0 = time.monotonic()
        messages = build_answer_messages(question, chunks)
        raw_answer = await self.llm.complete(messages)
        llm_ms = int((time.monotonic() - llm_t0) * 1000)

        ground = verify_grounding(raw_answer, chunks)
        if not ground.valid:
            ground = await self._regenerate(question, chunks, ground, route="hybrid")

        elapsed = int((time.monotonic() - t0) * 1000)
        return AnswerResult(
            question=question,
            answer=ground.answer,
            citations=ground.citations,
            chunks=list(chunks),
            outcome=outcome,
            latency=LatencyBreakdown(
                embed_ms=embed_ms,
                retrieve_ms=retrieve_ms,
                llm_ms=llm_ms,
                total_ms=elapsed,
            ),
            grounded=ground.valid,
            graph_context=community_context,
        )

    # ------------------------------------------------------- community route

    async def _run_community(
        self,
        *,
        question: str,
        t0: float,
        top_k: int,
        repo: str | None,
    ) -> AnswerResult | _Degrade:
        """GraphRAG community route.

        Steps:
        1. Embed the question (cosine over community vectors).
        2. `CommunityRetriever.search` → top-K most relevant communities.
        3. Pull representative seed chunks per community so the answer
           can ground on real `[path:line]` references.
        4. Build the community prompt, complete, verify grounding,
           optionally regenerate (DD-013).

        Any of (no retriever, no hits, no chunks) returns `_Degrade` and
        the dispatcher re-runs on hybrid with `degraded_from="community"`.
        """
        del top_k, repo  # community route does not slice by repo or top_k yet

        if self.community is None or self._embedder is None:
            return _Degrade(reason="community route not configured")

        embed_t0 = time.monotonic()
        qvec = self._embedder.embed([question])[0]
        embed_ms = int((time.monotonic() - embed_t0) * 1000)

        retrieve_t0 = time.monotonic()
        hits = await self.community.search(qvec.tolist(), top_k=self.community_top_k)
        if not hits:
            return _Degrade(
                reason="community retriever returned no hits",
                embed_ms=embed_ms,
            )

        chunks = self._chunks_for_communities(hits)
        retrieve_ms = int((time.monotonic() - retrieve_t0) * 1000)
        if not chunks:
            return _Degrade(
                reason="no chunks available for selected communities",
                embed_ms=embed_ms,
                community_context=self._hits_to_context(hits),
            )

        llm_t0 = time.monotonic()
        messages = build_community_answer_messages(
            question,
            communities=[(h.community_id, h.level, h.title, h.summary) for h in hits],
            chunks=chunks,
        )
        raw_answer = await self.llm.complete(messages)
        llm_ms = int((time.monotonic() - llm_t0) * 1000)

        ground = verify_grounding(raw_answer, chunks)
        if not ground.valid:
            ground = await self._regenerate(question, chunks, ground, route="community")

        elapsed = int((time.monotonic() - t0) * 1000)
        return AnswerResult(
            question=question,
            answer=ground.answer,
            citations=ground.citations,
            chunks=list(chunks),
            outcome=RouteOutcome(route="community"),
            latency=LatencyBreakdown(
                embed_ms=embed_ms,
                retrieve_ms=retrieve_ms,
                llm_ms=llm_ms,
                total_ms=elapsed,
            ),
            grounded=ground.valid,
            graph_context=self._hits_to_context(hits),
        )

    def _chunks_for_communities(self, hits: Sequence[ScoredCommunity]) -> list[RetrievedChunk]:
        """Materialise seed chunks for a batch of community hits.

        We pull at most `community_chunks_per_hit` rows per community
        (ordered by chunk span — bigger functions / classes usually
        carry more signal). Chunks are deduped by `chunk_id` so a chunk
        owned by two communities isn't quoted twice in the prompt.

        Leaf-name extraction is done in Python rather than SQL — SQLite
        doesn't ship a portable rfind, and Python's `rsplit` is trivial.
        """
        if not hits:
            return []
        per_hit = max(1, self.community_chunks_per_hit)
        store = CommunityStore(self.sqlite_path)
        seen_ids: set[str] = set()
        chunks_out: list[RetrievedChunk] = []
        try:
            store.init_schema()
            conn = store._connect()
            for hit in hits:
                # Prefer `is_seed=1` rows (level-0 leaves where the
                # summariser marked representative members). Higher-
                # level rolled-up communities have no seeds — fall back
                # to all members so they can still surface chunks.
                seed_rows = conn.execute(
                    "SELECT fqn FROM community_members WHERE community_id = ? AND is_seed = 1",
                    (hit.community_id,),
                ).fetchall()
                if not seed_rows:
                    seed_rows = conn.execute(
                        "SELECT fqn FROM community_members WHERE community_id = ?",
                        (hit.community_id,),
                    ).fetchall()
                if not seed_rows:
                    continue
                seeds = [r[0] for r in seed_rows]
                leaves = [s.rsplit(".", 1)[-1] for s in seeds]
                if not leaves:
                    continue
                placeholders = ",".join("?" * len(leaves))
                rows = conn.execute(
                    f"""
                    SELECT chunk_id, repo, path, language, start_line,
                           end_line, symbol, text,
                           (end_line - start_line + 1) AS span
                    FROM chunks
                    WHERE symbol IN ({placeholders})
                    ORDER BY span DESC, path, start_line
                    LIMIT ?
                    """,
                    (*leaves, per_hit * 4),  # over-fetch so dedup leaves enough
                ).fetchall()
                taken = 0
                for row in rows:
                    if taken >= per_hit:
                        break
                    chunk_id = row[0]
                    if chunk_id in seen_ids:
                        continue
                    seen_ids.add(chunk_id)
                    taken += 1
                    chunks_out.append(
                        RetrievedChunk(
                            chunk_id=chunk_id,
                            repo=row[1],
                            path=Path(row[2]),
                            start_line=row[4],
                            end_line=row[5],
                            text=row[7],
                            symbol=row[6],
                            score=1.0,
                            source="rrf",
                        )
                    )
        finally:
            store.close()
        return chunks_out

    @staticmethod
    def _hits_to_context(
        hits: Sequence[ScoredCommunity],
    ) -> list[CommunityContextItem]:
        return [
            CommunityContextItem(
                community_id=h.community_id,
                level=h.level,
                title=h.title,
                summary=h.summary,
                score=h.score,
            )
            for h in hits
        ]

    async def _regenerate(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        bad: GroundingResult,
        *,
        route: str = "hybrid",
    ) -> GroundingResult:
        """One-shot regeneration with bad citations called out.

        DD-013: we do *not* loop. If the second attempt also fabricates,
        we return the answer with the bad citations stripped so callers
        always have something to show.
        """
        # ``route`` is logged so operators can tell whether the community
        # path is hallucinating more than the hybrid baseline — a common
        # GraphRAG failure mode (too many summary snippets, LLM citing
        # them as if they were code locations).
        logger.warning(
            "grounding failed [route=%s]: %d bad citations on first attempt; regenerating",
            route,
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
            "grounding still failed after regeneration [route=%s]; stripping %d bad citations",
            route,
            len(verified.dropped_citations),
        )
        cleaned = strip_bad_citations(verified.answer, verified.dropped_citations)
        return GroundingResult(
            answer=cleaned,
            citations=verified.citations,
            dropped_citations=verified.dropped_citations,
            valid=False,
        )
