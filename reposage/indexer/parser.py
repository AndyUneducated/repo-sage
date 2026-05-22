"""tree-sitter wrapper that yields a typed `ParseResult` per file.

We keep the parser deliberately language-agnostic: language-specific queries
live in `queries/<lang>.scm` and are loaded lazily.

Phase 1 wires up Python only end-to-end. TypeScript / JavaScript / Go grammars
are still loaded so that callers can verify a file is parseable, but their
symbol-extraction queries are deferred to a later phase. See
`docs/plans/phase-1-indexer.md` for the agreed TS/Go behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import tree_sitter

Language = Literal["python", "typescript", "javascript", "go"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("python", "typescript", "javascript", "go")

# Skip ridiculous files (generated, vendored bundles, etc.) — tree-sitter
# can technically handle them but they distort indexing time and rarely
# contain useful symbols.
DEFAULT_MAX_BYTES = 1_000_000


@dataclass(slots=True, frozen=True)
class ParseResult:
    path: Path
    language: Language
    source: bytes
    # `tree_sitter.Tree`. The annotation is strictly forward (we never import
    # `tree_sitter` at module import time, so module loading stays cheap).
    tree: tree_sitter.Tree

    @property
    def has_error(self) -> bool:
        """Whether the syntax tree contains any ERROR or MISSING nodes."""
        return bool(self.tree.root_node.has_error)


class TreeSitterParser:
    """Lazy multi-language parser. Holds one cached `Parser` per language."""

    def __init__(
        self,
        languages: tuple[Language, ...] = SUPPORTED_LANGUAGES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.languages = languages
        self.max_bytes = max_bytes
        self._language_cache: dict[Language, tree_sitter.Language] = {}
        self._parser_cache: dict[Language, tree_sitter.Parser] = {}

    def detect_language(self, path: Path) -> Language | None:
        ext = path.suffix.lower()
        by_ext: dict[str, Language] = {
            ".py": "python",
            ".pyi": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".go": "go",
        }
        return by_ext.get(ext)

    def _get_language(self, lang: Language) -> tree_sitter.Language:
        cached = self._language_cache.get(lang)
        if cached is not None:
            return cached
        from tree_sitter_language_pack import get_language

        # `tree_sitter_language_pack` accepts the same names as our `Language`
        # literal (python, typescript, javascript, go).
        loaded = get_language(lang)
        self._language_cache[lang] = loaded
        return loaded

    def _get_parser(self, lang: Language) -> tree_sitter.Parser:
        cached = self._parser_cache.get(lang)
        if cached is not None:
            return cached
        import tree_sitter

        parser = tree_sitter.Parser(self._get_language(lang))
        self._parser_cache[lang] = parser
        return parser

    def parse(self, path: Path) -> ParseResult | None:
        """Parse a single file.

        Returns `None` for unsupported extensions, files larger than
        `max_bytes`, or files we cannot read. A `ParseResult` is returned for
        successfully parsed files, *including* those whose tree contains
        ERROR nodes — the caller decides whether to treat that as a hard
        failure (see `ParseResult.has_error`).
        """
        lang = self.detect_language(path)
        if lang is None or lang not in self.languages:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size > self.max_bytes:
            return None
        try:
            source = path.read_bytes()
        except OSError:
            return None
        parser = self._get_parser(lang)
        tree = parser.parse(source)
        return ParseResult(path=path, language=lang, source=source, tree=tree)
