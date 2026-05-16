"""End-to-end indexing pipeline orchestration.

Stages (deliberately linear; parallelism added per-stage in later phases):

    walk repo  ->  parse  ->  chunk  ->  embed  ->  symbol graph
                                                  └─> community detect
                                                       └─> summarise

Stage failures degrade gracefully: a parse error on one file should not abort
the run. Errors are logged and surfaced in the final index manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class IndexManifest:
    repo: str
    n_files: int = 0
    n_chunks: int = 0
    n_symbols: int = 0
    n_edges: int = 0
    n_communities: int = 0
    failures: list[str] | None = None


class IndexPipeline:
    """Build everything needed to answer questions for one repository."""

    def __init__(self, repo: Path, repo_name: str | None = None) -> None:
        self.repo = repo
        self.repo_name = repo_name or repo.name

    def run(self, force: bool = False) -> IndexManifest:
        # Phase 1: parse + chunk + embed + symbol graph.
        # Phase 3: GraphRAG community detection + summaries.
        raise NotImplementedError
