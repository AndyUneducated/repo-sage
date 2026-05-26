"""Map-Reduce LLM-generated summaries for each community.

Pipeline (Microsoft GraphRAG-shaped, but with `content_sha` cache):

* **Map**  (level 0):
    For each leaf community we pick seed FQNs (`graphrag.seed`), pull
    their chunk bodies from SQLite, and ask the LLM to produce a JSON
    `{title, summary}` describing the community.

* **Reduce** (level 1..N):
    For each non-leaf community we gather the *already-generated*
    summaries of its child communities and ask the LLM to roll them up
    into a coarser-grained `{title, summary}`. Reduce never re-reads
    code chunks: it operates purely over child summaries.

`asyncio.Semaphore(concurrency)` bounds the number of in-flight LLM
calls so local Ollama doesn't queue and remote APIs don't burn the
rate limit. Each community's call awaits inside `_summarise_one`, so
ordering is non-deterministic but writes go through SQLite (which is
process-serialised).

The summariser is **idempotent**: passing it the same list of
`Community` rows whose `content_sha` already lives in the store skips
the LLM entirely. This is what makes incremental re-indexing tractable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import replace

from reposage.indexer.graphrag.community import Community
from reposage.indexer.graphrag.seed import fqns_only, pick_seed_members
from reposage.llm.prompts import (
    build_community_reduce_messages,
    build_community_summary_messages,
)
from reposage.retrieval.protocols import LLMClient

logger = logging.getLogger(__name__)

# Hard caps so a pathological community can't blow the LLM context.
_MAX_SEED_CHUNK_LINES = 80
_MAX_CHILDREN_PER_REDUCE = 12
_MIN_SUMMARY_CHARS = 20
_PLACEHOLDER_SUMMARY = "<auto-summary unavailable>"
_PLACEHOLDER_TITLE = "Untitled community"


class CommunitySummarizer:
    """Annotate `Community` objects with `(title, summary)`.

    Construct once per indexing run and call `summarize_all`; the
    instance carries the LLM client, model id, and concurrency limiter.
    """

    def __init__(
        self,
        llm: LLMClient,
        *,
        max_seeds: int = 12,
        max_tokens: int = 600,
        concurrency: int = 4,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self.llm = llm
        self.max_seeds = max_seeds
        self.max_tokens = max_tokens
        self._semaphore = asyncio.Semaphore(concurrency)
        self._model = getattr(llm, "model", "unknown")

    # ----------------------------------------------------- public API

    async def summarize_all(
        self,
        communities: Iterable[Community],
        *,
        conn: sqlite3.Connection,
        existing: dict[str, Community] | None = None,
    ) -> list[Community]:
        """Map+Reduce over the given communities.

        ``conn`` is reused both for seed/chunk lookups and for any
        `existing` cache fed in by the caller. ``existing`` maps a
        community's `content_sha` to a previously-summarised Community
        whose `(title, summary)` can be copied directly. Passing
        ``existing`` lets us skip LLM calls across re-indexes.
        """
        comms = list(communities)
        if not comms:
            return []
        existing = existing or {}
        by_id: dict[int, Community] = {c.id: c for c in comms}

        # Process levels in ascending order: leaves first (Map), then
        # parents (Reduce) once their children have summaries.
        by_level: dict[int, list[Community]] = {}
        for c in comms:
            by_level.setdefault(c.level, []).append(c)

        for level in sorted(by_level.keys()):
            level_comms = by_level[level]
            results = await asyncio.gather(
                *[
                    self._summarise_one(c, conn=conn, by_id=by_id, existing=existing)
                    for c in level_comms
                ]
            )
            for r in results:
                by_id[r.id] = r

        return [by_id[c.id] for c in comms]

    # ---------------------------------------------------- one community

    async def _summarise_one(
        self,
        c: Community,
        *,
        conn: sqlite3.Connection,
        by_id: dict[int, Community],
        existing: dict[str, Community],
    ) -> Community:
        # Cache hit: same content_sha already has a non-empty summary.
        prior = existing.get(c.content_sha)
        if prior is not None and prior.summary and prior.summary != _PLACEHOLDER_SUMMARY:
            logger.debug("summary cache hit for community %s (sha=%s)", c.id, c.content_sha[:12])
            return replace(
                c,
                title=prior.title,
                summary=prior.summary,
                summary_model=prior.summary_model or self._model,
            )

        async with self._semaphore:
            try:
                if c.level == 0:
                    title, summary = await self._map(c, conn=conn)
                else:
                    title, summary = await self._reduce(c, by_id=by_id)
            except Exception as exc:
                # Hard-fail-soft: log, write a placeholder, keep going.
                # The community route can still surface the title (if any)
                # and downstream eval will mark grounding misses.
                logger.warning("summarisation failed for community %s: %r", c.id, exc)
                return replace(
                    c,
                    title=c.title or _PLACEHOLDER_TITLE,
                    summary=_PLACEHOLDER_SUMMARY,
                    summary_model=self._model,
                )

        if not summary or len(summary) < _MIN_SUMMARY_CHARS:
            summary = _PLACEHOLDER_SUMMARY
        return replace(c, title=title, summary=summary, summary_model=self._model)

    # -------------------------------------------------------- Map level

    async def _map(
        self,
        c: Community,
        *,
        conn: sqlite3.Connection,
    ) -> tuple[str, str]:
        seeds = pick_seed_members(c, conn=conn, max_seeds=self.max_seeds)
        seed_fqns = fqns_only(seeds)
        chunks = self._fetch_seed_chunks(seed_fqns, conn=conn)
        messages = build_community_summary_messages(
            members=c.members, seeds=seed_fqns, seed_chunks=chunks, level=c.level
        )
        raw = await self.llm.complete(messages)
        return _parse_summary_json(raw)

    def _fetch_seed_chunks(
        self, seed_fqns: list[str], *, conn: sqlite3.Connection
    ) -> list[tuple[str, str, int, int, str]]:
        """Yield ``(path, symbol, start, end, text)`` for each seed.

        We resolve by `symbol` column on `chunks` because Phase 1 stores
        the bare name, not the FQN — same trick `pick_seed_members`
        already uses. Each chunk is truncated to
        `_MAX_SEED_CHUNK_LINES` to bound prompt size.
        """
        if not seed_fqns:
            return []
        names = [f.rsplit(".", 1)[-1] for f in seed_fqns]
        placeholders = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT path, symbol, start_line, end_line, text FROM chunks "
            f"WHERE symbol IN ({placeholders}) ORDER BY symbol, start_line",
            tuple(names),
        ).fetchall()
        out: list[tuple[str, str, int, int, str]] = []
        for path, symbol, start, end, text in rows:
            lines = text.splitlines()
            if len(lines) > _MAX_SEED_CHUNK_LINES:
                truncated_lines = [
                    *lines[:_MAX_SEED_CHUNK_LINES],
                    f"# ... truncated {len(lines) - _MAX_SEED_CHUNK_LINES} lines ...",
                ]
                trimmed = "\n".join(truncated_lines)
            else:
                trimmed = text
            out.append((path, symbol, start, end, trimmed))
        return out

    # ----------------------------------------------------- Reduce level

    async def _reduce(
        self,
        c: Community,
        *,
        by_id: dict[int, Community],
    ) -> tuple[str, str]:
        # Use already-summarised children; if a child failed and wrote a
        # placeholder, skip it rather than poisoning the parent.
        children_summaries: list[tuple[str | None, str]] = []
        for cid in c.child_ids[:_MAX_CHILDREN_PER_REDUCE]:
            child = by_id.get(cid)
            if child is None:
                continue
            if not child.summary or child.summary == _PLACEHOLDER_SUMMARY:
                continue
            children_summaries.append((child.title, child.summary))
        if not children_summaries:
            # No usable child summaries — return a minimal placeholder
            # so the Reduce LLM call isn't wasted.
            return _PLACEHOLDER_TITLE, _PLACEHOLDER_SUMMARY
        messages = build_community_reduce_messages(
            child_summaries=children_summaries, level=c.level
        )
        raw = await self.llm.complete(messages)
        return _parse_summary_json(raw)


# --------------------------------------------------------------- parsing


def _parse_summary_json(raw: str) -> tuple[str, str]:
    """Extract ``{title, summary}`` from a possibly noisy LLM response.

    Same defensive parsing pattern as `QueryRouter._llm_route`: strip
    code fences, find the first ``{...}`` block, parse it, and accept
    plausible string values.

    On parse failure we fall back to using the raw text as the summary
    and a synthesised title. The caller's length guard will then either
    accept it or write the placeholder.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            decoded = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            title = str(decoded.get("title") or "").strip() or _PLACEHOLDER_TITLE
            summary = str(decoded.get("summary") or "").strip()
            return title, summary
    # Fallback: treat the entire response as the summary body.
    summary = raw.strip()
    title = _PLACEHOLDER_TITLE if not summary else summary.splitlines()[0][:80].strip()
    return title, summary


# ------------------------------------- back-compat alias for the old stub
#
# Phase 1 / 2 code paths that imported the stub used `summarize(...)`. The
# new entry point is `summarize_all`; keep the old name as a shim that
# raises a clearer error than the original `NotImplementedError`.


def summarize_legacy_stub(_: object) -> None:  # pragma: no cover
    raise RuntimeError("CommunitySummarizer.summarize was renamed to summarize_all in Phase 3")
