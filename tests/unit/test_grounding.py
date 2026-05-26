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


# ---------- LLM-dialect tolerance (Postel's law) ----------
#
# Small local models (qwen2.5-coder:7b et al.) frequently echo the
# `<retrieved_chunk path="...">` attribute syntax back into their
# citations. The parser must normalise these variants to the canonical
# Citation so grounding doesn't reject them as fabricated.


def test_extract_xml_attr_form_with_double_quotes() -> None:
    """`[path="…":lo-hi]` is the most common 7B dialect."""
    cites = extract_citations('see [path="app/api/middleware.py":8-13] ok')
    assert cites == [Citation(path="app/api/middleware.py", start_line=8, end_line=13)]


def test_extract_xml_attr_form_with_single_quotes() -> None:
    cites = extract_citations("[path='a/b.py':10-12]")
    assert cites == [Citation(path="a/b.py", start_line=10, end_line=12)]


def test_extract_bare_double_quoted_path() -> None:
    cites = extract_citations('["a/b.py":10-12]')
    assert cites == [Citation(path="a/b.py", start_line=10, end_line=12)]


def test_extract_backtick_quoted_path() -> None:
    cites = extract_citations("[`a/b.py`:10-12]")
    assert cites == [Citation(path="a/b.py", start_line=10, end_line=12)]


def test_extract_rejects_mismatched_quotes() -> None:
    """Opening `"` must close with `"`, not `'`."""
    assert extract_citations("[path=\"a/b.py':10-12]") == []


def test_xml_attr_form_is_grounded_against_canonical_chunk() -> None:
    """The chunk path is canonical (`a/b.py`); the citation in XML-attr
    form must still resolve to that path after normalisation."""
    chunks = [_chunk("app/api/middleware.py", 8, 13)]
    result = verify_grounding('see [path="app/api/middleware.py":8-13]', chunks)
    assert result.valid
    assert result.citations == [Citation(path="app/api/middleware.py", start_line=8, end_line=13)]
    assert result.dropped_citations == []


def test_strip_handles_xml_attr_form() -> None:
    """A dropped XML-attr citation must still be stripped from the
    original answer — the literal token has quotes and `path=` in it."""
    bad = [Citation(path="x.py", start_line=10, end_line=20)]
    out = strip_bad_citations('see [path="x.py":10-20] there', bad)
    assert out == "see [citation removed] there"
