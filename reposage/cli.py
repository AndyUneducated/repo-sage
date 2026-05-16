"""Typer CLI for local development: `reposage index ...`, `reposage ask ...`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint

from reposage import __version__
from reposage.config import get_settings

app = typer.Typer(
    name="reposage",
    help="Repository-level code Q&A.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    if version:
        rprint(f"reposage {__version__}")
        raise typer.Exit()


@app.command()
def index(
    repo: Path = typer.Option(..., exists=True, file_okay=False, help="Path to repo to index."),
    languages: str = typer.Option("python,typescript,go", help="Comma-separated languages."),
    force: bool = typer.Option(False, help="Re-index even if the index already exists."),
) -> None:
    """Build symbol graph + embeddings + community summaries for a repo."""
    _ = get_settings()
    rprint(f"[bold]Indexing[/bold] {repo} (langs={languages}, force={force})")
    raise typer.Exit(code=0)  # pragma: no cover  -- Phase 1 wires this up


@app.command()
def ask(
    question: str = typer.Argument(..., help="Free-form natural language question."),
    repo: Path | None = typer.Option(None, help="Repo to scope the answer to (default: latest)."),
    top_k: int = typer.Option(8, min=1, max=50, help="Hybrid retriever top-k."),
) -> None:
    """One-shot Q&A against an indexed repository."""
    _ = get_settings()
    rprint(f"[bold]Question:[/bold] {question}")
    rprint(f"[dim]repo={repo} top_k={top_k}[/dim]")
    raise typer.Exit(code=0)  # pragma: no cover -- Phase 2 wires this up


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Enable autoreload."),
) -> None:
    """Run the FastAPI server (mirrors `make dev`)."""
    import uvicorn

    uvicorn.run("reposage.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":  # pragma: no cover
    app()
