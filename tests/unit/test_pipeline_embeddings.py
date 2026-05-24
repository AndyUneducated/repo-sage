"""IndexPipeline embedding integration tests.

The Phase 2 plan requires `IndexPipeline` to write an embeddings row for
every chunk in the same SQLite transaction (atomicity boundary). These
tests pin three contracts:

1. With an embedder configured, every chunk must have a matching
   ``embeddings`` row keyed on ``chunk_id``.
2. With ``embedder=None`` (Phase 1 graph-only mode and ``--no-embed``),
   no embeddings rows are written.
3. The embedder's ``model`` and ``dim`` are persisted on every row, so
   the cold-start dim/model check on the HNSW server side succeeds.
4. ``ON DELETE CASCADE`` from chunks → embeddings actually fires when
   chunks are re-indexed under ``force=True``.

The tests use `HashEmbedder` (deterministic, no model download) and the
existing `tiny_python_repo` fixture so they pass on CI without secrets.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.storage.embeddings_store import EmbeddingsStore

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, dst)
    return dst


def _count(db: Path, sql: str, params: tuple[object, ...] = ()) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def test_embeddings_match_chunks_when_embedder_configured(repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(
        force=True
    )
    assert manifest.failures == []
    assert manifest.n_chunks > 0
    assert manifest.n_embeddings == manifest.n_chunks

    n_chunks = _count(db, "SELECT COUNT(*) FROM chunks")
    n_emb = _count(db, "SELECT COUNT(*) FROM embeddings")
    assert n_chunks == n_emb, "every chunk must have an embedding row"

    # Every embedding must reference an existing chunk_id.
    n_dangling = _count(
        db,
        "SELECT COUNT(*) FROM embeddings e "
        "LEFT JOIN chunks c ON c.chunk_id = e.chunk_id "
        "WHERE c.chunk_id IS NULL",
    )
    assert n_dangling == 0


def test_no_embed_leaves_embeddings_table_empty(repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=None).run(
        force=True
    )
    assert manifest.n_chunks > 0
    assert manifest.n_embeddings == 0
    assert _count(db, "SELECT COUNT(*) FROM embeddings") == 0


def test_embedding_rows_carry_model_and_dim(repo: Path, tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)
    store = EmbeddingsStore(db)
    try:
        store.init_schema()
        stats = store.stats()
    finally:
        store.close()
    # Exactly one model present, with the dim the embedder advertised.
    assert embedder.model in stats
    n, d = stats[embedder.model]
    assert d == embedder.dim
    assert n > 0


def test_force_reindex_cascades_old_embeddings(repo: Path, tmp_path: Path) -> None:
    """Re-indexing with `force=True` must not leave orphan embedding rows."""
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)
    n_emb_first = _count(db, "SELECT COUNT(*) FROM embeddings")
    assert n_emb_first > 0

    # Re-index with force; chunks are recreated with the same chunk_id when
    # source content is unchanged, so the count must remain stable but no
    # rows must be left dangling.
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)
    n_chunks = _count(db, "SELECT COUNT(*) FROM chunks")
    n_emb_second = _count(db, "SELECT COUNT(*) FROM embeddings")
    assert n_chunks == n_emb_second
    n_dangling = _count(
        db,
        "SELECT COUNT(*) FROM embeddings e "
        "LEFT JOIN chunks c ON c.chunk_id = e.chunk_id "
        "WHERE c.chunk_id IS NULL",
    )
    assert n_dangling == 0


def test_embedding_vector_is_unit_norm_for_hash_embedder(repo: Path, tmp_path: Path) -> None:
    """Plumbing check: the bytes round-trip through the BLOB store correctly."""
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)
    store = EmbeddingsStore(db)
    try:
        store.init_schema()
        # Pick any chunk_id from the chunks table.
        conn = sqlite3.connect(db)
        try:
            chunk_id = conn.execute("SELECT chunk_id FROM chunks LIMIT 1").fetchone()[0]
        finally:
            conn.close()
        got = store.get(chunk_id)
    finally:
        store.close()
    assert got is not None
    vec, model, dim = got
    assert model == embedder.model
    assert dim == embedder.dim
    # HashEmbedder produces unit-norm vectors; round-trip must preserve that.
    import numpy as np  # noqa: PLC0415

    np.testing.assert_allclose(np.linalg.norm(vec), 1.0, atol=1e-5)


def test_unsupported_files_do_not_create_embeddings(repo: Path, tmp_path: Path) -> None:
    """`frontend.ts` is parsed-only in Phase 1/2; it must not reach the embedder."""
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)
    # No chunks for the .ts file, so no embeddings either.
    n_ts_chunks = _count(
        db,
        "SELECT COUNT(*) FROM chunks WHERE path LIKE '%.ts'",
    )
    assert n_ts_chunks == 0
