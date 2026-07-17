"""Citation extraction + grounding verification.

Phase 2 enforces a strict contract: every ``[path:lo-hi]`` reference in the
LLM answer must overlap a chunk that was actually placed in the context.
The check is line-level, not character-level, so the LLM may legitimately
narrow a range (``[a.py:30-32]`` from a chunk spanning ``25-50``) but
cannot reference content that was never retrieved.

Failure modes:

* No citations at all     -> caller may decide; we don't reject.
* Citation matches a chunk -> kept.
* Citation matches no chunk -> dropped; the original answer is regenerated
  exactly once (DD-013) with the offending citations removed from the
  context. If the regenerated answer also fails, we return the cleaned
  answer with the bad citations stripped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from reposage.retrieval.hybrid import RetrievedChunk

# Accept the canonical `[path:lo-hi]` form plus the common LLM dialects:
#
#   [path/to/file.py:10-20]            ← canonical
#   [path="path/to/file.py":10-20]     ← XML-attr style; small open models
#                                        regularly copy this from the
#                                        `<retrieved_chunk path="..." ...>`
#                                        headers we put in the prompt.
#   [path='path/to/file.py':10-20]     ← same with single quotes
#   ["path/to/file.py":10-20]          ← quoted path without `path=`
#   [`path/to/file.py`:10-20]          ← backtick-quoted (markdown reflex)
#
# Postel's law: be liberal in what we accept, then normalise to the
# canonical Citation(path, lo, hi). `verify_grounding` only ever sees
# the canonical form, so the grounding check stays simple.
CITATION_RE = re.compile(
    r"""
    \[
        \s*
        (?:path\s*=\s*)?                            # optional `path=` prefix
        (?P<quote>["'`])?                           # optional opening quote
        # Path segments separated by `/`. `/` is excluded from the segment
        # class so the separator is unambiguous — with `/` *inside* the class
        # the `seg+(?:/seg+)*` shape backtracks catastrophically on inputs
        # like `[/a/a/a…` (ReDoS). Each `/` now has exactly one parse.
        (?P<path>[^\s\]:"'`/]+(?:/[^\s\]:"'`/]+)*)  # path token(s)
        (?(quote)(?P=quote))                        # closing quote iff opened
        \s*:\s*
        (?P<lo>\d+)
        \s*-\s*
        (?P<hi>\d+)
        \s*
    \]
    """,
    re.VERBOSE,
)


@dataclass(slots=True, frozen=True)
class Citation:
    path: str
    start_line: int
    end_line: int


def _parse_match(m: re.Match[str]) -> Citation | None:
    try:
        lo = int(m.group("lo"))
        hi = int(m.group("hi"))
    except ValueError:
        return None
    if lo <= 0 or hi < lo:
        return None
    return Citation(path=m.group("path"), start_line=lo, end_line=hi)


def extract_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for m in CITATION_RE.finditer(text):
        c = _parse_match(m)
        if c is not None:
            out.append(c)
    return out


def _ranges_by_path(chunks: Iterable[RetrievedChunk]) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for c in chunks:
        out.setdefault(str(c.path), []).append((c.start_line, c.end_line))
    return out


def is_grounded(citation: Citation, ranges_by_path: dict[str, list[tuple[int, int]]]) -> bool:
    spans = ranges_by_path.get(citation.path)
    if not spans:
        return False
    # The citation is grounded if it is fully contained in at least one
    # retrieved chunk. We do not allow partial overlaps because a partial
    # overlap means the LLM is reaching for content we never sent.
    return any(lo <= citation.start_line and citation.end_line <= hi for lo, hi in spans)


@dataclass(slots=True, frozen=True)
class GroundingResult:
    answer: str
    citations: list[Citation]
    dropped_citations: list[Citation]
    valid: bool


def verify_grounding(answer: str, chunks: Iterable[RetrievedChunk]) -> GroundingResult:
    """Return ``GroundingResult`` describing whether ``answer`` is grounded.

    ``valid`` is True iff every citation in the answer matches a retrieved
    chunk. Callers use ``dropped_citations`` to feed the regeneration prompt
    or to surface a warning to the user.
    """
    cited = extract_citations(answer)
    ranges = _ranges_by_path(chunks)
    kept: list[Citation] = []
    dropped: list[Citation] = []
    for c in cited:
        if is_grounded(c, ranges):
            kept.append(c)
        else:
            dropped.append(c)
    return GroundingResult(
        answer=answer,
        citations=kept,
        dropped_citations=dropped,
        valid=not dropped,
    )


def strip_bad_citations(answer: str, dropped: Iterable[Citation]) -> str:
    """Remove the textual citation span for every dropped citation.

    Used as a last-resort fallback when both the original and regenerated
    answers contain bad citations. The span is whatever ``CITATION_RE``
    matched — that way the canonical ``[path:lo-hi]`` form *and* the
    quoted / `path="..."` dialects all get stripped cleanly.
    """
    bad = {(c.path, c.start_line, c.end_line) for c in dropped}
    if not bad:
        return answer

    def _replace(m: re.Match[str]) -> str:
        c = _parse_match(m)
        if c is not None and (c.path, c.start_line, c.end_line) in bad:
            return "[citation removed]"
        return m.group(0)

    return CITATION_RE.sub(_replace, answer)
