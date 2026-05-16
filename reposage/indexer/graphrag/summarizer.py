"""LLM-generated natural-language summaries for each community."""

from __future__ import annotations

from collections.abc import Iterable

from reposage.indexer.graphrag.community import Community


class CommunitySummarizer:
    """Map each `Community` → short natural-language summary suitable for prompting."""

    def __init__(self, max_tokens: int = 600) -> None:
        self.max_tokens = max_tokens

    def summarize(self, communities: Iterable[Community]) -> list[Community]:
        # Phase 3: pull representative chunks for each community member,
        # prompt the LLM, and attach the result to a copy of the Community.
        raise NotImplementedError
