"""Typer CLI for local development: `reposage index ...`, `reposage ask ...`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from reposage import __version__
from reposage.config import get_settings
from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.router import QueryRouter
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

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
    languages: str = typer.Option(
        "python", help="Comma-separated languages (Phase 1: only 'python' produces graph rows)."
    ),
    force: bool = typer.Option(False, help="Re-index even if the index already exists."),
    sqlite_path: Path | None = typer.Option(
        None, help="Override the SQLite index path (defaults to settings.sqlite_path)."
    ),
    repo_name: str | None = typer.Option(
        None, help="Logical repo name (defaults to the directory name)."
    ),
) -> None:
    """Build symbol graph + chunks for a repo."""
    settings = get_settings()
    db_path = sqlite_path or settings.sqlite_path
    pipeline = IndexPipeline(repo=repo, sqlite_path=db_path, repo_name=repo_name)
    rprint(f"[bold]Indexing[/bold] {repo} (langs={languages}, force={force})")
    manifest = pipeline.run(force=force)

    table = Table(title=f"Index manifest for {manifest.repo}")
    table.add_column("metric", style="cyan")
    table.add_column("value", justify="right")
    table.add_row("files seen", str(manifest.n_files))
    table.add_row("python files", str(manifest.n_python_files))
    table.add_row("unsupported files", str(manifest.n_unsupported_files))
    table.add_row("parse errors", str(manifest.n_parse_errors))
    table.add_row("chunks", str(manifest.n_chunks))
    table.add_row("symbols (nodes)", str(manifest.n_symbols))
    table.add_row("edges", str(manifest.n_edges))
    table.add_row("elapsed (s)", f"{manifest.elapsed_seconds:.3f}")
    rprint(table)

    if manifest.failures:
        rprint(f"[yellow]{len(manifest.failures)} files failed (showing up to 5):[/yellow]")
        for line in manifest.failures[:5]:
            rprint(f"  [red]{line}[/red]")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Free-form natural language question."),
    repo: Path | None = typer.Option(None, help="Repo to scope the answer to (default: latest)."),
    top_k: int = typer.Option(8, min=1, max=50, help="Hybrid retriever top-k (Phase 2)."),
    sqlite_path: Path | None = typer.Option(
        None, help="Override the SQLite index path (defaults to settings.sqlite_path)."
    ),
    route: str | None = typer.Option(
        None,
        "--route",
        help="Force a route ('graph'). Other routes are Phase 2.",
    ),
) -> None:
    """One-shot Q&A against an indexed repository."""
    settings = get_settings()
    db_path = sqlite_path or settings.sqlite_path

    router = QueryRouter()
    if route is None:
        try:
            decision = router.route_sync(question)
        except NotImplementedError:
            rprint("[yellow]No symbolic name detected; non-graph routes are Phase 2.[/yellow]")
            raise typer.Exit(code=1) from None
    elif route == "graph":
        symbol = router.detect_symbol(question)
        if symbol is None:
            rprint(
                "[yellow]No dotted symbol like 'Foo.bar' found in question; "
                "graph route needs one. Try `--route hybrid` in Phase 2.[/yellow]"
            )
            raise typer.Exit(code=1)
        from reposage.retrieval.router import QueryRoute  # local import to keep startup light

        decision = QueryRoute(name="graph", confidence=1.0, reason="forced", symbol=symbol)
    else:
        rprint(f"[red]Route {route!r} not supported in Phase 1.[/red]")
        raise typer.Exit(code=2)

    if decision.name != "graph":
        rprint(
            "[yellow]Phase 1 CLI only serves the graph route. "
            "Phase 2 wires up hybrid + LLM answering.[/yellow]"
        )
        raise typer.Exit(code=1)

    assert decision.symbol is not None
    _ = repo  # repo scoping is single-repo today; kept for forward-compat.
    _ = top_k  # unused by graph route
    _print_callers(question=question, symbol=decision.symbol, sqlite_path=db_path)


def _print_callers(*, question: str, symbol: str, sqlite_path: Path) -> None:
    store = SQLiteSymbolGraphStore(sqlite_path)
    store.init_schema()
    candidates = store.find_nodes_by_suffix(symbol)
    if not candidates:
        rprint(f"[red]No node found for {symbol!r} in {sqlite_path}.[/red]")
        rprint(
            "[dim]Tip: did you run `reposage index --repo <path>` first? "
            "Or try a fully-qualified name (pkg.mod.Class.method).[/dim]"
        )
        raise typer.Exit(code=1)
    rprint(f"[bold]Q:[/bold] {question}")
    for node in candidates:
        rprint(f"\n[bold cyan]{node.fqn}[/bold cyan]  ({node.path}:{node.start_line})")
        callers = store.callers_of(node.fqn)
        if not callers:
            rprint("  [dim]no callers found in indexed code[/dim]")
            continue
        table = Table(show_header=True, header_style="bold")
        table.add_column("caller", style="green")
        table.add_column("path:line")
        for e in callers:
            table.add_row(e.src, f"{e.src_path}:{e.src_line}")
        rprint(table)


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
