"""Unit tests for Markdown citation rendering + commit-SHA permalinks (DD-053)."""

from __future__ import annotations

from pathlib import Path

from reposage.bot.citation import CitationBuilder
from reposage.retrieval.hybrid import RetrievedChunk


def _chunk(path: str, lo: int, hi: int, chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        repo="acme/widgets",
        path=Path(path),
        start_line=lo,
        end_line=hi,
        text="...",
        symbol=None,
        score=1.0,
        source="rrf",
    )


def test_from_chunks_dedupes_by_span() -> None:
    builder = CitationBuilder("acme/widgets", "deadbeef")
    cites = builder.from_chunks(
        [
            _chunk("app/auth.py", 10, 20, "c1"),
            _chunk("app/auth.py", 10, 20, "c2"),  # same span → deduped
            _chunk("app/db.py", 1, 5, "c3"),
        ]
    )
    assert [(c.path, c.start_line, c.end_line) for c in cites] == [
        ("app/auth.py", 10, 20),
        ("app/db.py", 1, 5),
    ]


def test_permalink_anchors_to_commit_sha_not_head() -> None:
    builder = CitationBuilder("acme/widgets", "abc123")
    [cite] = builder.from_chunks([_chunk("app/auth.py", 10, 20, "c1")])
    url = builder.permalink(cite)
    assert url == "https://github.com/acme/widgets/blob/abc123/app/auth.py#L10-L20"
    assert "HEAD" not in url


def test_render_markdown_lists_sources() -> None:
    builder = CitationBuilder("acme/widgets", "abc123")
    cites = builder.from_chunks([_chunk("app/auth.py", 10, 20, "c1")])
    md = builder.render_markdown(cites)
    assert md.startswith("**Sources:**")
    assert "`app/auth.py:10-20`" in md
    assert "(https://github.com/acme/widgets/blob/abc123/app/auth.py#L10-L20)" in md


def test_render_markdown_empty_is_blank() -> None:
    builder = CitationBuilder("acme/widgets", "abc123")
    assert builder.render_markdown([]) == ""
