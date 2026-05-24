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

CITATION_RE = re.compile(
    r"\[(?P<path>[^\s\]:]+(?:/[^\s\]:]+)*)\s*:\s*(?P<lo>\d+)\s*-\s*(?P<hi>\d+)\]"
)


@dataclass(slots=True, frozen=True)
class Citation:
    path: str
    start_line: int
    end_line: int


def extract_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for m in CITATION_RE.finditer(text):
        try:
            lo = int(m.group("lo"))
            hi = int(m.group("hi"))
        except ValueError:
            continue
        if lo <= 0 or hi < lo:
            continue
        out.append(Citation(path=m.group("path"), start_line=lo, end_line=hi))
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
    """Remove the textual `[path:lo-hi]` for every dropped citation.

    Used as a last-resort fallback when both the original and regenerated
    answers contain bad citations.
    """
    out = answer
    for c in dropped:
        marker = f"[{c.path}:{c.start_line}-{c.end_line}]"
        out = out.replace(marker, "[citation removed]")
    return out
