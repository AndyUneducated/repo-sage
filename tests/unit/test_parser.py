"""Unit tests for `reposage.indexer.parser`."""

from __future__ import annotations

from pathlib import Path

import pytest
from reposage.indexer.parser import TreeSitterParser


def _write(tmp_path: Path, name: str, body: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_python_file_parses_clean(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    path = _write(
        tmp_path,
        "demo.py",
        b"def foo(x):\n    return x + 1\n\nclass Bar(Base):\n    def m(self):\n        return foo(1)\n",
    )
    result = parser.parse(path)
    assert result is not None
    assert result.language == "python"
    assert not result.has_error
    assert result.tree.root_node.type == "module"


def test_typescript_file_parses_clean(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    path = _write(tmp_path, "frontend.ts", b"export const x: number = 1;\n")
    result = parser.parse(path)
    assert result is not None
    assert result.language == "typescript"
    assert not result.has_error


def test_go_file_parses_clean(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    path = _write(tmp_path, "main.go", b"package main\n\nfunc Foo() int { return 1 }\n")
    result = parser.parse(path)
    assert result is not None
    assert result.language == "go"
    assert not result.has_error


def test_unsupported_extension_returns_none(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    path = _write(tmp_path, "README.md", b"# hi\n")
    assert parser.parse(path) is None


def test_oversized_file_returns_none(tmp_path: Path) -> None:
    parser = TreeSitterParser(max_bytes=128)
    path = _write(tmp_path, "huge.py", b"x = 1\n" * 200)
    assert parser.parse(path) is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    parser = TreeSitterParser()
    assert parser.parse(tmp_path / "ghost.py") is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.py", "python"),
        ("a.pyi", "python"),
        ("a.ts", "typescript"),
        ("a.tsx", "typescript"),
        ("a.js", "javascript"),
        ("a.jsx", "javascript"),
        ("a.go", "go"),
        ("a.rs", None),
        ("a", None),
    ],
)
def test_detect_language(filename: str, expected: str | None) -> None:
    parser = TreeSitterParser()
    assert parser.detect_language(Path(filename)) == expected
