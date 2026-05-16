"""tree-sitter wrapper that yields a typed `ParseResult` per file.

We keep the parser deliberately language-agnostic: language-specific queries
live in `queries/<lang>.scm` (added in Phase 1) and are loaded lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Language = Literal["python", "typescript", "javascript", "go"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("python", "typescript", "javascript", "go")


@dataclass(slots=True, frozen=True)
class ParseResult:
    path: Path
    language: Language
    source: bytes
    # Phase 1: hold the tree-sitter Tree; kept opaque here to avoid importing
    # tree_sitter at module import time.
    tree: object


class TreeSitterParser:
    """Lazy multi-language parser. Holds one `Parser` per language."""

    def __init__(self, languages: tuple[Language, ...] = SUPPORTED_LANGUAGES) -> None:
        self.languages = languages

    def detect_language(self, path: Path) -> Language | None:
        ext = path.suffix.lower()
        by_ext: dict[str, Language] = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".go": "go",
        }
        return by_ext.get(ext)

    def parse(self, path: Path) -> ParseResult | None:
        """Parse a single file. Returns `None` for unsupported extensions."""
        # Phase 1: implement using tree_sitter_languages.
        raise NotImplementedError
