"""Versioned in-process answer cache (Phase 9, DD-046).

A bounded LRU keyed on ``(repo_version, question, route_hint, top_k,
model)``. ``repo_version`` folds in ``repo_meta.head_sha`` /
``last_indexed_at`` so *any* re-index invalidates every cached answer for
that repo without an explicit purge — the classic "content-addressed by the
index's own version" trick.

The cache is **opt-in** (``settings.answer_cache_enabled``, default off) so
the default CLI / test behaviour is byte-for-byte unchanged, mirroring the
OTel opt-in (DD-030). It only ever stores *grounded* answers: caching an
ungrounded/regenerated failure would pin a bad result until the next index.

Not thread-safe by design — the API serves requests on one event loop and
the LRU ops are O(1) non-async; if a threaded server is ever introduced this
grows a lock (noted in phase-9-latency.md §Risks).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import replace

from reposage.services.retrieval_service import AnswerResult, LatencyBreakdown

_UNIT_SEP = "\x1f"


class AnswerCache:
    """Bounded LRU of :class:`AnswerResult` keyed by a versioned digest."""

    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("answer cache capacity must be >= 1")
        self._capacity = capacity
        self._data: OrderedDict[str, AnswerResult] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(
        *,
        repo: str | None,
        repo_version: str | None,
        question: str,
        route_hint: str | None,
        top_k: int,
        model: str,
    ) -> str:
        """Stable digest of everything that can change an answer.

        ``repo_version`` is ``None`` when the repo has no ``repo_meta`` row
        yet (never indexed); we still produce a key so within-process reuse
        works, but such entries are naturally superseded once a real version
        appears. ``repo`` is folded in so two never-indexed repos (both with
        ``repo_version=None``) can't collide.
        """
        raw = _UNIT_SEP.join(
            [
                repo or "",
                repo_version or "",
                question.strip(),
                route_hint or "auto",
                str(top_k),
                model,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> AnswerResult | None:
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        # Hand back a copy stamped as a (near-zero) cache hit so callers that
        # read `latency` don't report the original cold-path timings, and so a
        # caller mutating the result can't corrupt the cached entry.
        return replace(entry, latency=LatencyBreakdown(total_ms=0))

    def put(self, key: str, value: AnswerResult) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._capacity:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data
