"""Function/class-aware code chunking driven by tree-sitter spans.

We chunk on AST node boundaries (function, method, class, top-level statement)
rather than fixed token windows so embeddings stay coherent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reposage.indexer.parser import Language, ParseResult


@dataclass(slots=True, frozen=True)
class Chunk:
    repo: str
    path: Path
    language: Language
    text: str
    start_line: int
    end_line: int
    symbol: str | None
    parent_symbol: str | None


class Chunker:
    """Split `ParseResult` into semantically coherent code chunks."""

    def __init__(self, max_lines: int = 80, overlap_lines: int = 4) -> None:
        self.max_lines = max_lines
        self.overlap_lines = overlap_lines

    def chunk(self, repo: str, parsed: ParseResult) -> list[Chunk]:
        # Phase 1: walk the tree-sitter Tree, emit one Chunk per top-level
        # def/class; subdivide overlong functions with overlap.
        raise NotImplementedError
