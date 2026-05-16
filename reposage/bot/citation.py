"""Render `RetrievedChunk` lists as Markdown code citations for GitHub comments."""

from __future__ import annotations

from collections.abc import Sequence

from reposage.api.schemas import Citation
from reposage.retrieval.hybrid import RetrievedChunk


class CitationBuilder:
    def __init__(self, repo_url_template: str = "https://github.com/{repo}/blob/HEAD/{path}#L{start}-L{end}") -> None:
        self.repo_url_template = repo_url_template

    def from_chunks(self, chunks: Sequence[RetrievedChunk]) -> list[Citation]:
        return [
            Citation(
                repo=c.repo,
                path=str(c.path),
                start_line=c.start_line,
                end_line=c.end_line,
                score=c.score,
            )
            for c in chunks
        ]

    def render_markdown(self, citations: Sequence[Citation]) -> str:
        raise NotImplementedError
