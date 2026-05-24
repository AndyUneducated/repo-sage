"""End-to-end indexing pipeline orchestration.

Stages (deliberately linear; parallelism added per-stage in later phases):

    walk repo  ->  parse  ->  chunk  ->  graph extract / resolve
                                        └─> community detect (Phase 3)

Stage failures degrade gracefully: a parse error on one file should not abort
the run. Errors are logged and surfaced in the final index manifest.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from reposage.indexer.chunker import Chunk, Chunker
from reposage.indexer.embedder import EmbeddingProvider
from reposage.indexer.extractor import FileExtraction, PythonExtractor, module_fqn_for
from reposage.indexer.parser import ParseResult, TreeSitterParser
from reposage.indexer.python_resolver import PythonModuleResolver
from reposage.storage.chunk_store import ChunkStore
from reposage.storage.embeddings_store import EmbeddingsStore
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

logger = logging.getLogger(__name__)


SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "env",
        ".env",
        "__pycache__",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".cache",
        ".idea",
        ".vscode",
    }
)


@dataclass(slots=True)
class IndexManifest:
    repo: str
    n_files: int = 0
    n_python_files: int = 0
    n_unsupported_files: int = 0
    n_parse_errors: int = 0
    n_chunks: int = 0
    n_embeddings: int = 0
    n_symbols: int = 0
    n_edges: int = 0
    n_communities: int = 0
    elapsed_seconds: float = 0.0
    failures: list[str] = field(default_factory=list)


class IndexPipeline:
    """Build everything needed to answer questions for one repository.

    Phase 1 only wires up parse + chunk + symbol graph. Embedding push and
    community detection live in later phases.
    """

    def __init__(
        self,
        repo: Path,
        sqlite_path: Path,
        repo_name: str | None = None,
        max_file_bytes: int = 1_000_000,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.repo = repo.resolve()
        self.repo_name = repo_name or repo.name
        self.sqlite_path = sqlite_path
        self.parser = TreeSitterParser(max_bytes=max_file_bytes)
        self.chunker = Chunker()
        self.python_extractor = PythonExtractor()
        # Phase 2: optional. None means "don't write embeddings" — used by
        # Phase 1 graph-only tests and by the reposage CLI when --no-embed
        # is passed.
        self.embedder = embedder

    def run(self, force: bool = False) -> IndexManifest:
        t0 = time.monotonic()
        manifest = IndexManifest(repo=self.repo_name)
        graph_store = SQLiteSymbolGraphStore(self.sqlite_path)
        chunk_store = ChunkStore(self.sqlite_path)
        embeddings_store = EmbeddingsStore(self.sqlite_path)
        try:
            graph_store.init_schema()
            chunk_store.init_schema()
            embeddings_store.init_schema()
            if force:
                graph_store.clear_repo(self.repo_name)
                # Embeddings cascade off chunks via FK; deleting chunks first
                # also evicts orphaned embedding rows for this repo.
                chunk_store.clear_repo(self.repo_name)

            python_extractions: list[FileExtraction] = []
            for path in self._walk_files():
                manifest.n_files += 1
                try:
                    self._index_file(
                        path=path,
                        graph_store=graph_store,
                        chunk_store=chunk_store,
                        embeddings_store=embeddings_store,
                        python_extractions=python_extractions,
                        manifest=manifest,
                        force=force,
                    )
                except Exception as exc:
                    manifest.failures.append(f"{path}: {exc!r}")
                    manifest.n_parse_errors += 1
                    logger.exception("indexing failed for %s", path)

            if python_extractions:
                resolver = PythonModuleResolver(repo=self.repo_name)
                graph = resolver.resolve(python_extractions)
                manifest.n_symbols += graph_store.upsert_nodes(graph.nodes)
                manifest.n_edges += graph_store.upsert_edges(graph.edges)

            graph_store.upsert_repo_meta(repo=self.repo_name)
        finally:
            graph_store.close()
            chunk_store.close()
            embeddings_store.close()
        manifest.elapsed_seconds = round(time.monotonic() - t0, 3)
        return manifest

    # ------------------------------------------------------------------ helpers

    def _walk_files(self) -> Iterable[Path]:
        """Yield all files under ``self.repo`` not inside a SKIP_DIRS directory."""
        for path in self._iter_paths(self.repo):
            if path.is_file():
                yield path

    def _iter_paths(self, root: Path) -> Iterable[Path]:
        # Top-level repo dir is always traversed even if it sits inside a hidden
        # parent (common for fixtures). The guard below only fires on subdirs.
        if (root.name in SKIP_DIRS or root.name.startswith(".")) and root != self.repo:
            return
        try:
            children = list(root.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir():
                if child.name in SKIP_DIRS:
                    continue
                yield from self._iter_paths(child)
            else:
                yield child

    def _index_file(
        self,
        *,
        path: Path,
        graph_store: SQLiteSymbolGraphStore,
        chunk_store: ChunkStore,
        embeddings_store: EmbeddingsStore,
        python_extractions: list[FileExtraction],
        manifest: IndexManifest,
        force: bool,
    ) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        try:
            data = path.read_bytes()
        except OSError:
            return
        file_sha = hashlib.sha1(data, usedforsecurity=False).hexdigest()

        rel_path_str = self._rel_path(path)

        if not force:
            existing = graph_store.get_file_sha(self.repo_name, rel_path_str)
            if existing == file_sha:
                # Nothing changed; touch last_indexed_at and move on.
                graph_store.upsert_file_meta(
                    repo=self.repo_name,
                    path=rel_path_str,
                    file_sha=file_sha,
                    mtime=int(stat.st_mtime),
                    parse_status="cached",
                )
                return

        lang = self.parser.detect_language(path)
        if lang is None:
            return  # not a source file at all (txt/img/etc.)

        if lang != "python":
            # Phase 1: TS/Go are parse-only for visibility; no chunks/nodes/edges.
            graph_store.upsert_file_meta(
                repo=self.repo_name,
                path=rel_path_str,
                file_sha=file_sha,
                mtime=int(stat.st_mtime),
                parse_status="unsupported",
            )
            manifest.n_unsupported_files += 1
            return

        parsed = self.parser.parse(path)
        if parsed is None:
            graph_store.upsert_file_meta(
                repo=self.repo_name,
                path=rel_path_str,
                file_sha=file_sha,
                mtime=int(stat.st_mtime),
                parse_status="parse_error",
            )
            manifest.n_parse_errors += 1
            return
        if parsed.has_error:
            # Some Python files (e.g. with syntax errors) yield a partial tree.
            # We still chunk what we can; the resolver tolerates partial inputs.
            logger.debug("parse tree has errors: %s", path)

        manifest.n_python_files += 1

        # Re-key by repo-relative path so two checkouts of the same repo
        # produce identical FQNs/chunk_ids.
        parsed_rel = self._reroot(parsed, rel_path_str)

        chunks = self.chunker.chunk(self.repo_name, parsed_rel)
        if chunks:
            # Drop stale chunks for this file before inserting fresh ones.
            # Embeddings cascade via the FK we set up in EmbeddingsStore.
            chunk_store.delete_by_path(self.repo_name, rel_path_str)
            chunk_store.upsert(chunks, file_sha=file_sha)
            manifest.n_chunks += len(chunks)
            if self.embedder is not None:
                manifest.n_embeddings += self._embed_and_store(chunks, embeddings_store)

        module = module_fqn_for(self.repo, path)
        # Pass the rerooted ParseResult so the extractor stamps every RawDef /
        # RawEdge with the repo-relative path — keeps node `path` columns
        # portable across machines.
        extraction = self.python_extractor.extract(parsed_rel, file_module=module)
        python_extractions.append(extraction)

        graph_store.upsert_file_meta(
            repo=self.repo_name,
            path=rel_path_str,
            file_sha=file_sha,
            mtime=int(stat.st_mtime),
            parse_status="ok",
        )

    def _embed_and_store(self, chunks: list[Chunk], embeddings_store: EmbeddingsStore) -> int:
        embedder = self.embedder
        assert embedder is not None  # guarded by caller
        vectors = embedder.embed([c.text for c in chunks])
        if vectors.shape[0] != len(chunks):
            raise RuntimeError(
                f"embedder returned {vectors.shape[0]} rows for {len(chunks)} chunks"
            )
        return embeddings_store.upsert(
            ((chunk.chunk_id, vectors[i]) for i, chunk in enumerate(chunks)),
            model=embedder.model,
            dim=embedder.dim,
        )

    def _rel_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.repo))
        except ValueError:
            return str(path)

    def _reroot(self, parsed: ParseResult, rel_path_str: str) -> ParseResult:
        return ParseResult(
            path=Path(rel_path_str),
            language=parsed.language,
            source=parsed.source,
            tree=parsed.tree,
        )
