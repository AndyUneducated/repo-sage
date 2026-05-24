"""Unit tests for citation extraction + grounding verification."""

from __future__ import annotations

from pathlib import Path

from reposage.llm.grounding import (
    Citation,
    extract_citations,
    is_grounded,
    strip_bad_citations,
    verify_grounding,
)
from reposage.retrieval.hybrid import RetrievedChunk


def _chunk(path: str, lo: int, hi: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{path}:{lo}-{hi}",
        repo="r",
        path=Path(path),
        start_line=lo,
        end_line=hi,
        text="...",
        symbol=None,
        score=1.0,
        source="rrf",
    )


def test_extract_simple() -> None:
    assert extract_citations("see [a/b.py:10-20]") == [
        Citation(path="a/b.py", start_line=10, end_line=20)
    ]


def test_extract_multiple() -> None:
    txt = "See [auth/sessions.py:12-25] and also [api/routes.py:30-31]."
    cites = extract_citations(txt)
    assert len(cites) == 2
    assert cites[0].path == "auth/sessions.py"
    assert cites[1].start_line == 30


def test_extract_rejects_inverted_range() -> None:
    assert extract_citations("[x.py:30-10]") == []


def test_extract_rejects_non_positive() -> None:
    assert extract_citations("[x.py:0-5]") == []


def test_grounded_within_chunk() -> None:
    chunks = [_chunk("a.py", 10, 20)]
    result = verify_grounding("yes [a.py:12-15] indeed", chunks)
    assert result.valid
    assert len(result.citations) == 1
    assert result.dropped_citations == []


def test_partial_overlap_is_not_grounded() -> None:
    """Overlap is not enough — full containment is required.

    The motivation: if the LLM cites lines outside the retrieved chunk, it
    is reaching for content we never sent. Better to fail loudly.
    """
    chunks = [_chunk("a.py", 10, 20)]
    result = verify_grounding("[a.py:18-25]", chunks)
    assert not result.valid
    assert len(result.dropped_citations) == 1


def test_unknown_path_dropped() -> None:
    chunks = [_chunk("a.py", 10, 20)]
    result = verify_grounding("[other.py:10-20]", chunks)
    assert not result.valid
    assert result.dropped_citations[0].path == "other.py"


def test_is_grounded_on_multiple_chunks_same_path() -> None:
    cites = extract_citations("[a.py:12-15] [a.py:55-58]")
    by_path = {"a.py": [(10, 20), (50, 60)]}
    assert all(is_grounded(c, by_path) for c in cites)


def test_strip_bad_citations() -> None:
    bad = [Citation(path="x.py", start_line=10, end_line=20)]
    out = strip_bad_citations("see [x.py:10-20] there", bad)
    assert out == "see [citation removed] there"


def test_no_citations_at_all_is_not_failure() -> None:
    chunks = [_chunk("a.py", 10, 20)]
    result = verify_grounding("just some prose", chunks)
    assert result.valid
    assert result.citations == []
    assert result.dropped_citations == []
