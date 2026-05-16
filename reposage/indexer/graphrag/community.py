"""Leiden community detection on the symbol graph.

Why Leiden over Louvain: Leiden guarantees well-connected communities and
yields more stable partitions when the graph is iteratively re-indexed.
"""

from __future__ import annotations

from dataclasses import dataclass

from reposage.indexer.symbol_graph import SymbolGraph


@dataclass(slots=True, frozen=True)
class Community:
    id: int
    members: tuple[str, ...]  # symbol FQNs
    level: int  # hierarchy depth (Leiden produces nested)
    parent_id: int | None
    summary: str | None = None  # filled in by `CommunitySummarizer`


class CommunityDetector:
    """Run Leiden against the symbol graph and emit a hierarchical partition."""

    def __init__(self, resolution: float = 1.0, max_levels: int = 3, seed: int = 1337) -> None:
        self.resolution = resolution
        self.max_levels = max_levels
        self.seed = seed

    def detect(self, graph: SymbolGraph) -> list[Community]:
        # Phase 3: build an igraph Graph from `graph`, run leidenalg with
        # ModularityVertexPartition or RBConfigurationVertexPartition,
        # recurse for `max_levels` to get hierarchy.
        raise NotImplementedError
