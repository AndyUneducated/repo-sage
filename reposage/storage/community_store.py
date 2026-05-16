"""Persistent store for GraphRAG communities + LLM-generated summaries."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from reposage.indexer.graphrag.community import Community


class CommunityStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def init_schema(self) -> None:
        raise NotImplementedError

    def upsert(self, communities: Iterable[Community]) -> None:
        raise NotImplementedError

    def find_by_member(self, fqn: str) -> list[Community]:
        raise NotImplementedError

    def top_level(self) -> list[Community]:
        raise NotImplementedError
