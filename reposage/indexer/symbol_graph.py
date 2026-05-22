"""Symbol graph: definitions / calls / inheritance / imports.

Stored as adjacency tables in SQLite (see `reposage.storage.sqlite_graph`).
The graph itself is *the* answer to deterministic questions like
"where is `User.login` called?" — no LLM needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EdgeKind = Literal["def", "call", "inherit", "import"]
EDGE_KINDS: tuple[EdgeKind, ...] = ("def", "call", "inherit", "import")


SymbolKind = Literal["module", "class", "function", "method", "variable"]


@dataclass(slots=True, frozen=True)
class SymbolNode:
    fqn: str  # fully-qualified name, e.g. `pkg.module.Class.method`
    kind: SymbolKind
    language: Literal["python", "typescript", "javascript", "go"]
    repo: str
    path: str
    start_line: int
    end_line: int


@dataclass(slots=True, frozen=True)
class SymbolEdge:
    src: str  # source FQN
    dst: str  # destination FQN
    kind: EdgeKind
    src_path: str
    src_line: int


class SymbolGraph:
    """In-memory builder; persisted via `SQLiteSymbolGraphStore`."""

    def __init__(self) -> None:
        self.nodes: dict[str, SymbolNode] = {}
        self.edges: list[SymbolEdge] = []

    def add_node(self, node: SymbolNode) -> None:
        self.nodes[node.fqn] = node

    def add_edge(self, edge: SymbolEdge) -> None:
        self.edges.append(edge)

    def in_neighbours(self, fqn: str, kind: EdgeKind | None = None) -> list[SymbolEdge]:
        """Reverse adjacency, e.g. who calls `User.login`?"""
        return [e for e in self.edges if e.dst == fqn and (kind is None or e.kind == kind)]

    def out_neighbours(self, fqn: str, kind: EdgeKind | None = None) -> list[SymbolEdge]:
        return [e for e in self.edges if e.src == fqn and (kind is None or e.kind == kind)]
