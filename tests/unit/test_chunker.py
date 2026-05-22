"""Unit tests for `reposage.indexer.chunker`."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from reposage.indexer.chunker import Chunker, make_chunk_id
from reposage.indexer.parser import TreeSitterParser


def _parse(tmp_path: Path, body: bytes) -> tuple[TreeSitterParser, Path]:
    parser = TreeSitterParser()
    path = tmp_path / "demo.py"
    path.write_bytes(body)
    return parser, path


def test_emits_one_chunk_per_top_level_function(tmp_path: Path) -> None:
    parser, path = _parse(
        tmp_path,
        b"def foo():\n    return 1\n\ndef bar():\n    return 2\n",
    )
    parsed = parser.parse(path)
    assert parsed is not None
    chunks = Chunker().chunk("demo", parsed)
    symbols = sorted(c.symbol for c in chunks if c.symbol is not None)
    assert symbols == ["bar", "foo"]
    assert all(c.parent_symbol is None for c in chunks)


def test_methods_get_class_as_parent_symbol(tmp_path: Path) -> None:
    parser, path = _parse(
        tmp_path,
        b"class User:\n    def login(self):\n        return None\n    def check(self):\n        return True\n",
    )
    parsed = parser.parse(path)
    assert parsed is not None
    chunks = Chunker().chunk("demo", parsed)
    methods = [c for c in chunks if c.symbol in {"login", "check"}]
    assert len(methods) == 2
    assert all(c.parent_symbol == "User" for c in methods)


def test_long_function_splits_with_overlap(tmp_path: Path) -> None:
    body = b"def big():\n" + b"    x = 1\n" * 200 + b"    return x\n"
    parser, path = _parse(tmp_path, body)
    parsed = parser.parse(path)
    assert parsed is not None
    chunks = Chunker(max_lines=80, overlap_lines=4).chunk("demo", parsed)
    big_chunks = [c for c in chunks if c.symbol == "big"]
    assert len(big_chunks) >= 3
    # Each window respects max_lines.
    for c in big_chunks:
        assert c.end_line - c.start_line + 1 <= 80
    # Adjacent windows overlap by `overlap_lines` rows on the source.
    for prev, nxt in pairwise(big_chunks):
        # nxt.start_line should land within prev's range minus overlap.
        assert nxt.start_line == prev.start_line + (80 - 4)


def test_chunk_id_is_stable_for_same_text(tmp_path: Path) -> None:
    parser, path = _parse(tmp_path, b"def foo():\n    return 1\n")
    parsed = parser.parse(path)
    assert parsed is not None
    chunker = Chunker()
    a = chunker.chunk("demo", parsed)
    b = chunker.chunk("demo", parsed)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


def test_chunk_id_changes_with_text() -> None:
    a = make_chunk_id("demo", Path("foo.py"), 1, 5, "def foo(): pass")
    b = make_chunk_id("demo", Path("foo.py"), 1, 5, "def foo(): return 1")
    assert a != b


def test_typescript_returns_empty_in_phase_1(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    path = tmp_path / "x.ts"
    path.write_bytes(b"export const x = 1;\n")
    parsed = parser.parse(path)
    assert parsed is not None
    assert Chunker().chunk("demo", parsed) == []
