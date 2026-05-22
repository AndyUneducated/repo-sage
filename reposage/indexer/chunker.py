"""Function/class-aware code chunking driven by tree-sitter spans.

We chunk on AST node boundaries (function, method, class, top-level statement)
rather than fixed token windows so embeddings stay coherent.

Phase 1 implements Python only; the public `Chunker.chunk` accepts any
`ParseResult` but only emits useful chunks for `language='python'`. Other
languages return an empty list and the pipeline records them as
`parse_status='unsupported'` in `file_meta`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from reposage.indexer.parser import Language, ParseResult

if TYPE_CHECKING:
    import tree_sitter


@dataclass(slots=True, frozen=True)
class Chunk:
    chunk_id: str  # sha1(repo|path|start_line|end_line|text), stable across runs
    repo: str
    path: Path
    language: Language
    text: str
    start_line: int  # 1-based inclusive
    end_line: int  # 1-based inclusive
    symbol: str | None  # bare name of the enclosing def/class, or None
    parent_symbol: str | None  # for methods, the enclosing class name


def make_chunk_id(repo: str, path: Path, start_line: int, end_line: int, text: str) -> str:
    """Stable sha1 of (repo, path, line range, text). Used as PK in `chunks`."""
    h = hashlib.sha1(usedforsecurity=False)
    h.update(repo.encode("utf-8"))
    h.update(b"|")
    h.update(str(path).encode("utf-8"))
    h.update(b"|")
    h.update(f"{start_line}-{end_line}".encode())
    h.update(b"|")
    h.update(text.encode("utf-8", errors="replace"))
    return h.hexdigest()


def _node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _node_lines(node: tree_sitter.Node) -> tuple[int, int]:
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _name_of(node: tree_sitter.Node) -> str | None:
    """Return the bare identifier of a function_definition / class_definition."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    text = name_node.text
    if text is None:
        return None
    return str(text.decode("utf-8", errors="replace"))


def _unwrap_decorated(node: tree_sitter.Node) -> tree_sitter.Node:
    """Python's `decorated_definition` wraps a function/class; descend through it."""
    if node.type != "decorated_definition":
        return node
    for child in node.children:
        if child.type in {"function_definition", "class_definition"}:
            return child
    return node


class Chunker:
    """Split `ParseResult` into semantically coherent code chunks."""

    def __init__(self, max_lines: int = 80, overlap_lines: int = 4) -> None:
        if overlap_lines >= max_lines:
            raise ValueError("overlap_lines must be smaller than max_lines")
        self.max_lines = max_lines
        self.overlap_lines = overlap_lines

    def chunk(self, repo: str, parsed: ParseResult) -> list[Chunk]:
        if parsed.language != "python":
            return []
        chunks: list[Chunk] = []
        self._emit_module(repo, parsed, parsed.tree.root_node, chunks)
        return chunks

    def _emit_module(
        self,
        repo: str,
        parsed: ParseResult,
        module_node: tree_sitter.Node,
        out: list[Chunk],
    ) -> None:
        for child in module_node.named_children:
            inner = _unwrap_decorated(child)
            if inner.type == "function_definition":
                self._emit_function(repo, parsed, child, parent=None, out=out)
            elif inner.type == "class_definition":
                self._emit_class(repo, parsed, child, out)

    def _emit_class(
        self,
        repo: str,
        parsed: ParseResult,
        class_outer_node: tree_sitter.Node,
        out: list[Chunk],
    ) -> None:
        class_node = _unwrap_decorated(class_outer_node)
        class_name = _name_of(class_node)
        # Emit one chunk for the class declaration line + signature.
        # Splitting long classes: we emit the class header (start..body_start)
        # plus each method as its own chunk, so embeddings stay focused.
        body = class_node.child_by_field_name("body")
        if body is not None:
            header_start, _ = _node_lines(class_outer_node)
            header_end = max(header_start, body.start_point[0])  # 1-based row
            self._emit_span(
                repo,
                parsed,
                start_line=header_start,
                end_line=header_end,
                text=parsed.source[class_outer_node.start_byte : body.start_byte].decode(
                    "utf-8", errors="replace"
                ),
                symbol=class_name,
                parent_symbol=None,
                out=out,
            )
            for child in body.named_children:
                inner = _unwrap_decorated(child)
                if inner.type == "function_definition":
                    self._emit_function(repo, parsed, child, parent=class_name, out=out)
        else:
            self._emit_function_like(
                repo, parsed, class_outer_node, symbol=class_name, parent=None, out=out
            )

    def _emit_function(
        self,
        repo: str,
        parsed: ParseResult,
        func_outer_node: tree_sitter.Node,
        parent: str | None,
        out: list[Chunk],
    ) -> None:
        inner = _unwrap_decorated(func_outer_node)
        name = _name_of(inner)
        self._emit_function_like(repo, parsed, func_outer_node, symbol=name, parent=parent, out=out)

    def _emit_function_like(
        self,
        repo: str,
        parsed: ParseResult,
        node: tree_sitter.Node,
        symbol: str | None,
        parent: str | None,
        out: list[Chunk],
    ) -> None:
        start_line, end_line = _node_lines(node)
        text = _node_text(parsed.source, node)
        for sub_text, sub_start, sub_end in self._split_long(text, start_line, end_line):
            self._emit_span(
                repo,
                parsed,
                start_line=sub_start,
                end_line=sub_end,
                text=sub_text,
                symbol=symbol,
                parent_symbol=parent,
                out=out,
            )

    def _emit_span(
        self,
        repo: str,
        parsed: ParseResult,
        *,
        start_line: int,
        end_line: int,
        text: str,
        symbol: str | None,
        parent_symbol: str | None,
        out: list[Chunk],
    ) -> None:
        if not text.strip():
            return
        chunk_id = make_chunk_id(repo, parsed.path, start_line, end_line, text)
        out.append(
            Chunk(
                chunk_id=chunk_id,
                repo=repo,
                path=parsed.path,
                language=parsed.language,
                text=text,
                start_line=start_line,
                end_line=end_line,
                symbol=symbol,
                parent_symbol=parent_symbol,
            )
        )

    def _split_long(self, text: str, start_line: int, end_line: int) -> list[tuple[str, int, int]]:
        """Split overlong text into windows of `max_lines` with `overlap_lines` overlap.

        Returns triples `(text, start_line, end_line)` with 1-based inclusive
        line numbers. If the text fits, returns a single triple.
        """
        lines = text.split("\n")
        if len(lines) <= self.max_lines:
            return [(text, start_line, end_line)]
        out: list[tuple[str, int, int]] = []
        step = self.max_lines - self.overlap_lines
        i = 0
        while i < len(lines):
            window = lines[i : i + self.max_lines]
            if not window:
                break
            out.append(
                (
                    "\n".join(window),
                    start_line + i,
                    start_line + i + len(window) - 1,
                )
            )
            if i + self.max_lines >= len(lines):
                break
            i += step
        return out
