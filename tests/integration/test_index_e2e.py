"""End-to-end indexing test on the `tiny_python_repo` fixture.

The fixture is hand-built so this test asserts a *byte-exact* expected
shape. If the indexer becomes more or less permissive, this test will
fail and the change must be intentional.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from reposage.indexer.pipeline import IndexPipeline
from reposage.storage.chunk_store import ChunkStore
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


@pytest.fixture
def indexed_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the fixture into ``tmp_path`` and run the pipeline against it."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    manifest = IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny").run(force=True)
    assert manifest.failures == []
    assert manifest.n_python_files == 13
    assert manifest.n_unsupported_files == 1  # frontend.ts
    assert manifest.n_symbols >= 40
    return repo, db


def test_required_nodes_exist(indexed_fixture: tuple[Path, Path]) -> None:
    _, db = indexed_fixture
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    must_exist = [
        "app.auth.users.User",
        "app.auth.users.User.login",
        "app.auth.users.AdminUser",
        "app.auth.users.AdminUser.login",
        "app.auth.sessions.Session",
        "app.auth.sessions.make_session",
        "app.billing.invoices.Invoice",
        "app.billing.invoices.Invoice.issue",
        "app.billing.payments.Payment.authorize",
        "app.billing.payments.charge",
        "app.api.middleware.require_auth",
        "app.utils.logging.log",
        "app.utils.db.Connection",
    ]
    for fqn in must_exist:
        assert store.get_node(fqn) is not None, f"missing node: {fqn}"
    store.close()


def test_inheritance_resolves_within_repo(indexed_fixture: tuple[Path, Path]) -> None:
    _, db = indexed_fixture
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    edges = store.edges("app.auth.users.AdminUser", kind="inherit", direction="out")
    assert any(e.dst == "app.auth.users.User" for e in edges)
    edges_cc = store.edges("app.billing.payments.CreditCard", kind="inherit", direction="out")
    assert any(e.dst == "app.billing.payments.Payment" for e in edges_cc)
    store.close()


def test_classmethod_call_chain(indexed_fixture: tuple[Path, Path]) -> None:
    """`User.login(self.user)` in Session.open should land on User.login."""
    _, db = indexed_fixture
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    callers = store.callers_of("app.auth.users.User.login")
    callers_set = {c.src for c in callers}
    assert "app.auth.sessions.Session.open" in callers_set
    assert "app.api.routes.login_route" in callers_set
    store.close()


def test_log_is_called_everywhere(indexed_fixture: tuple[Path, Path]) -> None:
    _, db = indexed_fixture
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    callers = store.callers_of("app.utils.logging.log")
    # Sanity: at least 10 distinct callers across modules.
    assert len({c.src for c in callers}) >= 10
    store.close()


def test_typescript_recorded_as_unsupported(indexed_fixture: tuple[Path, Path]) -> None:
    _, db = indexed_fixture
    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    counts = store.parse_status_counts("tiny")
    assert counts.get("unsupported", 0) == 1
    assert counts.get("ok", 0) == 13
    store.close()


def test_chunks_persisted_for_python_only(indexed_fixture: tuple[Path, Path]) -> None:
    _, db = indexed_fixture
    chunks = ChunkStore(db)
    chunks.init_schema()
    rows = list(chunks.iter_for_repo("tiny"))
    assert rows, "expected non-zero chunks for python files"
    # No chunk should reference the .ts file.
    assert not any(str(c.path).endswith(".ts") for c in rows)
    chunks.close()


def test_idempotent_reindex(indexed_fixture: tuple[Path, Path]) -> None:
    repo, db = indexed_fixture
    pipeline = IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny")
    # Re-running without force is a no-op (file shas match cached values).
    second = pipeline.run(force=False)
    # All files become 'cached' because shas match — no chunks/symbols added.
    assert second.n_python_files == 0
    assert second.n_chunks == 0
    assert second.n_symbols == 0


def test_force_rebuild_clears_old_repo(indexed_fixture: tuple[Path, Path]) -> None:
    repo, db = indexed_fixture
    # Index a *different* repo into the same DB to confirm clear_repo isolation.
    other_repo = repo.parent / "other"
    other_repo.mkdir()
    (other_repo / "x.py").write_text("def y(): return 1\n")
    IndexPipeline(repo=other_repo, sqlite_path=db, repo_name="other").run(force=True)

    store = SQLiteSymbolGraphStore(db)
    store.init_schema()
    # Both repos coexist:
    assert store.get_node("app.auth.users.User") is not None
    assert store.get_node("x.y") is not None
    store.close()
