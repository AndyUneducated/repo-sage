"""SQLite adjacency-list store for the symbol graph.

Schema (Phase 1):

    nodes(fqn TEXT PK, kind TEXT, repo TEXT, path TEXT, start_line INT, end_line INT)
    edges(src TEXT, dst TEXT, kind TEXT, src_path TEXT, src_line INT,
          PRIMARY KEY (src, dst, kind, src_line))
    INDEX edges_dst_kind ON edges(dst, kind)        -- reverse adjacency
    INDEX edges_src_kind ON edges(src, kind)
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from reposage.indexer.symbol_graph import EdgeKind, SymbolEdge, SymbolNode


class SQLiteSymbolGraphStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def init_schema(self) -> None:
        raise NotImplementedError

    def upsert_nodes(self, nodes: Iterable[SymbolNode]) -> None:
        raise NotImplementedError

    def upsert_edges(self, edges: Iterable[SymbolEdge]) -> None:
        raise NotImplementedError

    def callers_of(self, fqn: str) -> list[SymbolEdge]:
        """Reverse adjacency on `kind = "call"`. The hot path for graph queries."""
        raise NotImplementedError

    def callees_of(self, fqn: str) -> list[SymbolEdge]:
        raise NotImplementedError

    def edges(self, fqn: str, kind: EdgeKind | None = None, direction: str = "out") -> list[SymbolEdge]:
        raise NotImplementedError
