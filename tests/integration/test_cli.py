"""CLI integration tests for ``reposage index`` and ``reposage ask``.

The Phase 1 CLI was tested implicitly through the indexing pipeline. Phase
2 added two new shapes — `index --no-embed` and `ask --route hybrid|graph`
— each with its own wiring path that the rest of the suite does not cover.

We use Typer's `CliRunner`, point the CLI at a copy of `tiny_python_repo`
in `tmp_path`, and force `REPOSAGE_PROFILE=mock` so neither network nor
Go binary is required (the mock profile pins both LLM and dense to
in-process backends).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from reposage.cli import app
from typer.testing import CliRunner

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_python_repo"


@pytest.fixture
def repo_and_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Copy fixture into tmp and force the mock profile so CLI is offline."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    monkeypatch.setenv("REPOSAGE_PROFILE", "mock")
    return repo, db


def test_index_command_writes_embeddings_by_default(
    repo_and_db: tuple[Path, Path],
) -> None:
    repo, db = repo_and_db
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "index",
            "--repo",
            str(repo),
            "--sqlite-path",
            str(db),
            "--repo-name",
            "tiny",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output
    # The manifest table prints both chunks and embeddings counts.
    assert "chunks" in result.output
    assert "embeddings" in result.output
    assert db.exists()

    # Verify embeddings actually landed.
    import sqlite3  # noqa: PLC0415

    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    finally:
        conn.close()
    assert n > 0, "expected `index` to populate the embeddings table"


def test_index_no_embed_skips_embeddings(repo_and_db: tuple[Path, Path]) -> None:
    repo, db = repo_and_db
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "index",
            "--repo",
            str(repo),
            "--sqlite-path",
            str(db),
            "--repo-name",
            "tiny",
            "--no-embed",
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output

    import sqlite3  # noqa: PLC0415

    conn = sqlite3.connect(db)
    try:
        # Schema is created lazily by EmbeddingsStore.init_schema(); table exists.
        n = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    finally:
        conn.close()
    assert n == 0, "--no-embed must not write any embedding rows"


def test_ask_route_hybrid_returns_grounded_answer(
    repo_and_db: tuple[Path, Path],
) -> None:
    """End-to-end: `reposage ask --route hybrid` must print an answer."""
    repo, db = repo_and_db
    runner = CliRunner()
    # 1. Index first so the ask has something to retrieve.
    res_idx = runner.invoke(
        app,
        [
            "index",
            "--repo",
            str(repo),
            "--sqlite-path",
            str(db),
            "--repo-name",
            "tiny",
            "--force",
        ],
    )
    assert res_idx.exit_code == 0, res_idx.output

    # 2. Ask in hybrid mode with the mock LLM. We don't assert on the LLM
    # text content (mock answer wording can change); we assert that the
    # CLI ran end-to-end and printed the route + a citation block.
    res_ask = runner.invoke(
        app,
        [
            "ask",
            "How does Session.open work?",
            "--repo",
            "tiny",
            "--sqlite-path",
            str(db),
            "--route",
            "hybrid",
            "--top-k",
            "4",
        ],
    )
    assert res_ask.exit_code == 0, res_ask.output
    assert "route=hybrid" in res_ask.output
    # Either Citations or "I do not have enough context": the mock returns
    # a citation when context exists. The fixture has session-related code
    # so a citation must appear.
    assert "Citations:" in res_ask.output


def test_ask_route_graph_short_circuits_on_dotted_symbol(
    repo_and_db: tuple[Path, Path],
) -> None:
    repo, db = repo_and_db
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "index",
            "--repo",
            str(repo),
            "--sqlite-path",
            str(db),
            "--repo-name",
            "tiny",
            "--force",
        ],
    )
    res = runner.invoke(
        app,
        [
            "ask",
            "where is User.login called?",
            "--sqlite-path",
            str(db),
            "--route",
            "graph",
        ],
    )
    assert res.exit_code == 0, res.output
    # The graph fast-path prints the FQN of any matching node.
    assert "login" in res.output.lower()


def test_ask_route_graph_errors_without_a_symbol(
    repo_and_db: tuple[Path, Path],
) -> None:
    """`--route graph` on free-form prose must error with exit code 1."""
    _, db = repo_and_db
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "ask",
            "what is the architecture",
            "--sqlite-path",
            str(db),
            "--route",
            "graph",
        ],
    )
    assert res.exit_code == 1, res.output
    assert "symbol" in res.output.lower()


def test_ask_unknown_route_errors(repo_and_db: tuple[Path, Path]) -> None:
    """Bad --route value exits 2 and explains valid choices."""
    _, db = repo_and_db
    runner = CliRunner()
    res = runner.invoke(
        app,
        [
            "ask",
            "anything",
            "--sqlite-path",
            str(db),
            "--route",
            "garbage",
        ],
    )
    assert res.exit_code == 2, res.output
    assert "Unknown route" in res.output


def test_help_lists_phase2_subcommands() -> None:
    """The CLI surfaces `index`, `ask`, and `serve` at the top level."""
    runner = CliRunner()
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "index" in res.output
    assert "ask" in res.output
    assert "serve" in res.output
