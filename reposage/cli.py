"""Typer CLI: `reposage index`, `reposage ask`, `reposage serve`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from reposage import __version__
from reposage.composition import (
    build_embedder,
    build_retrieval_service,
    build_summarizer_llm,
)
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
    no_embed: bool = typer.Option(
        False,
        "--no-embed",
        help="Skip embedding generation. Useful for graph-only Phase 1 demos.",
    ),
    graphrag: bool = typer.Option(
        True,
        "--graphrag/--no-graphrag",
        help=(
            "Run Phase 3 GraphRAG community detection + summarisation after "
            "the symbol graph is built. Disable with --no-graphrag to keep "
            "indexing fast (Phase 1/2 behaviour)."
        ),
    ),
) -> None:
    """Build symbol graph + chunks + embeddings (+ communities) for a repo."""
    settings = get_settings()
    if settings.otel_enabled:
        from reposage.observability.otel import setup_tracing

        setup_tracing(
            service_name=settings.otel_service_name,
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
    db_path = sqlite_path or settings.sqlite_path
    embedder = None if no_embed else build_embedder()
    summarizer_llm = build_summarizer_llm() if graphrag else None
    pipeline = IndexPipeline(
        repo=repo,
        sqlite_path=db_path,
        repo_name=repo_name,
        embedder=embedder,
        graphrag=graphrag,
        summarizer_llm=summarizer_llm,
        community_resolution=settings.community_resolution,
        community_max_levels=settings.community_max_levels,
        community_min_size=settings.community_min_size,
        community_summary_concurrency=settings.community_summary_concurrency,
    )
    rprint(
        f"[bold]Indexing[/bold] {repo} (langs={languages}, force={force}, "
        f"embed={embedder is not None}, graphrag={graphrag})"
    )
    manifest = pipeline.run(force=force)

    table = Table(title=f"Index manifest for {manifest.repo}")
    table.add_column("metric", style="cyan")
    table.add_column("value", justify="right")
    table.add_row("files seen", str(manifest.n_files))
    table.add_row("python files", str(manifest.n_python_files))
    table.add_row("unsupported files", str(manifest.n_unsupported_files))
    table.add_row("parse errors", str(manifest.n_parse_errors))
    if manifest.n_deleted_files:
        table.add_row("deleted files", str(manifest.n_deleted_files))
    table.add_row("chunks", str(manifest.n_chunks))
    table.add_row("embeddings", str(manifest.n_embeddings))
    table.add_row("symbols (nodes)", str(manifest.n_symbols))
    table.add_row("edges", str(manifest.n_edges))
    if graphrag:
        table.add_row("communities", str(manifest.n_communities))
        table.add_row("community levels", str(manifest.n_community_levels))
        table.add_row("community summaries", str(manifest.n_community_summaries))
        table.add_row("community embeddings", str(manifest.n_community_embeddings))
    table.add_row("elapsed (s)", f"{manifest.elapsed_seconds:.3f}")
    rprint(table)

    if manifest.failures:
        rprint(f"[yellow]{len(manifest.failures)} files failed (showing up to 5):[/yellow]")
        for line in manifest.failures[:5]:
            rprint(f"  [red]{line}[/red]")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Free-form natural language question."),
    repo: str | None = typer.Option(None, help="Repo name to scope the answer to."),
    top_k: int = typer.Option(8, min=1, max=50, help="Hybrid retriever top-k."),
    sqlite_path: Path | None = typer.Option(
        None, help="Override the SQLite index path (defaults to settings.sqlite_path)."
    ),
    route: str = typer.Option(
        "auto",
        "--route",
        help="Force a route ('graph' | 'hybrid' | 'community' | 'auto').",
    ),
) -> None:
    """One-shot Q&A against an indexed repository."""
    settings = get_settings()
    db_path = sqlite_path or settings.sqlite_path

    if route == "graph":
        router = QueryRouter()
        symbol = router.detect_symbol(question)
        if symbol is None:
            rprint(
                "[yellow]No dotted/snake symbol found in the question. "
                "Try `--route hybrid` for free-form questions.[/yellow]"
            )
            raise typer.Exit(code=1)
        _print_callers(question=question, symbol=symbol, sqlite_path=db_path)
        return

    if route not in {"auto", "hybrid", "community"}:
        rprint(
            f"[red]Unknown route {route!r}. Choose one of: auto | graph | hybrid | community.[/red]"
        )
        raise typer.Exit(code=2)

    asyncio.run(
        _run_hybrid_ask(
            question=question,
            repo=repo,
            top_k=top_k,
            sqlite_path=db_path,
            route_hint=route,
        )
    )


async def _run_hybrid_ask(
    *,
    question: str,
    repo: str | None,
    top_k: int,
    sqlite_path: Path,
    route_hint: str,
) -> None:
    """Build a `RetrievalService` and run one Q&A turn."""
    service = build_retrieval_service(sqlite_path=sqlite_path, repo=repo)
    try:
        result = await service.answer(
            question,
            repo=repo,
            route_hint=None if route_hint == "auto" else route_hint,
            top_k=top_k,
        )

        rprint(f"[bold]Q:[/bold] {result.question}")
        if result.outcome.degraded_from:
            route_display = (
                f"{result.outcome.route} "
                f"(degraded from {result.outcome.degraded_from}: "
                f"{result.outcome.degrade_reason})"
            )
        else:
            route_display = result.outcome.route
        rprint(
            f"[dim]route={route_display}  grounded={result.grounded}  "
            f"latency={result.latency.total_ms} ms[/dim]\n"
        )
        rprint(result.answer)
        if result.citations:
            rprint("\n[bold]Citations:[/bold]")
            for c in result.citations:
                rprint(f"  - {c.path}:{c.start_line}-{c.end_line}")
        if result.graph_context:
            rprint("\n[bold]Communities:[/bold]")
            for item in result.graph_context:
                title = item.title or "(untitled)"
                rprint(
                    f"  - [{item.community_id} L{item.level} score={item.score:.3f}] "
                    f"[cyan]{title}[/cyan]"
                )
    finally:
        # Drain LiteLLM telemetry while the loop is alive — `asyncio.run`
        # in the caller would otherwise close the loop with orphaned
        # `Logging.async_success_handler` coroutines (see
        # `LiteLLMClient.aclose`).
        await service.llm.aclose()


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
    store.close()


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
