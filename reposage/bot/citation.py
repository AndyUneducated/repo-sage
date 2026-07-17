"""Render `RetrievedChunk` / `Citation` lists as Markdown for GitHub comments.

Permalinks are anchored to a **commit SHA**, never ``HEAD`` (DD-053): a
``blob/HEAD`` link silently rots when the branch moves, so a citation that
was correct at answer-time would point at unrelated lines days later.
The builder is created per-answer with the repo's full name and the SHA the
index was built from.
"""

from __future__ import annotations

from collections.abc import Sequence

from reposage.api.schemas import Citation
from reposage.retrieval.hybrid import RetrievedChunk

_PERMALINK = "{base_url}/{repo}/blob/{sha}/{path}#L{start}-L{end}"


class CitationBuilder:
    def __init__(
        self,
        repo: str,
        commit_sha: str,
        *,
        base_url: str = "https://github.com",
    ) -> None:
        self.repo = repo
        self.commit_sha = commit_sha
        self.base_url = base_url.rstrip("/")

    def from_chunks(self, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
        """Project retrieved chunks onto the public `Citation` schema.

        Deduped by ``(path, start, end)`` — the dense and sparse branches
        frequently surface the same span, and a comment shouldn't list it
        twice.
        """
        seen: set[tuple[str, int, int]] = set()
        out: list[Citation] = []
        for c in chunks:
            key = (str(c.path), c.start_line, c.end_line)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Citation(
                    path=str(c.path),
                    start_line=c.start_line,
                    end_line=c.end_line,
                    chunk_id=c.chunk_id,
                )
            )
        return out

    def permalink(self, citation: Citation) -> str:
        return _PERMALINK.format(
            base_url=self.base_url,
            repo=self.repo,
            sha=self.commit_sha,
            path=citation.path,
            start=citation.start_line,
            end=citation.end_line,
        )

    def render_markdown(self, citations: Sequence[Citation]) -> str:
        """Render a ``Sources`` bullet list of commit-anchored permalinks.

        Returns ``""`` for an empty list so callers can append it
        unconditionally without producing a dangling header.
        """
        if not citations:
            return ""
        lines = ["**Sources:**"]
        for c in citations:
            label = f"`{c.path}:{c.start_line}-{c.end_line}`"
            lines.append(f"- [{label}]({self.permalink(c)})")
        return "\n".join(lines)
