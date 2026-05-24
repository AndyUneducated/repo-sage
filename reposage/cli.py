"""Typer CLI: `reposage index`, `reposage ask`, `reposage serve`."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from reposage import __version__
from reposage.config import get_settings
from reposage.indexer.embedder import EmbeddingProvider
from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.protocols import DenseRetriever
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
) -> None:
    """Build symbol graph + chunks + embeddings for a repo."""
    settings = get_settings()
    db_path = sqlite_path or settings.sqlite_path
    embedder = None if no_embed else _build_indexing_embedder()
    pipeline = IndexPipeline(
        repo=repo,
        sqlite_path=db_path,
        repo_name=repo_name,
        embedder=embedder,
    )
    rprint(
        f"[bold]Indexing[/bold] {repo} (langs={languages}, force={force}, embed={embedder is not None})"
    )
    manifest = pipeline.run(force=force)

    table = Table(title=f"Index manifest for {manifest.repo}")
    table.add_column("metric", style="cyan")
    table.add_column("value", justify="right")
    table.add_row("files seen", str(manifest.n_files))
    table.add_row("python files", str(manifest.n_python_files))
    table.add_row("unsupported files", str(manifest.n_unsupported_files))
    table.add_row("parse errors", str(manifest.n_parse_errors))
    table.add_row("chunks", str(manifest.n_chunks))
    table.add_row("embeddings", str(manifest.n_embeddings))
    table.add_row("symbols (nodes)", str(manifest.n_symbols))
    table.add_row("edges", str(manifest.n_edges))
    table.add_row("elapsed (s)", f"{manifest.elapsed_seconds:.3f}")
    rprint(table)

    if manifest.failures:
        rprint(f"[yellow]{len(manifest.failures)} files failed (showing up to 5):[/yellow]")
        for line in manifest.failures[:5]:
            rprint(f"  [red]{line}[/red]")


def _build_indexing_embedder() -> EmbeddingProvider:
    """Return an embedder instance based on env / settings.

    `REPOSAGE_LLM_PROVIDER=mock` swaps in `HashEmbedder` so smoke tests
    and CI-without-secrets can index a repo without downloading bge.
    """
    flag = os.environ.get("REPOSAGE_LLM_PROVIDER", "").lower()
    if flag in {"mock", "test", "stub"}:
        from reposage.indexer.embedder import HashEmbedder

        return HashEmbedder()
    from reposage.indexer.embedder import BgeEmbedder

    return BgeEmbedder()


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
    from reposage.api.dependencies import (
        build_embedder,
        build_llm,
        build_reranker,
    )
    from reposage.retrieval.bm25 import BM25SparseRetriever
    from reposage.services.retrieval_service import RetrievalService

    embedder = build_embedder()
    sparse = BM25SparseRetriever.from_sqlite(sqlite_path, repo=repo)
    dense = _build_cli_dense(embedder=embedder, sqlite_path=sqlite_path, repo=repo)
    service = RetrievalService(
        sqlite_path=sqlite_path,
        embedder=embedder,
        dense=dense,
        sparse=sparse,
        reranker=build_reranker(),
        llm=build_llm(),
    )
    result = await service.answer(
        question,
        repo=repo,
        route_hint=None if route_hint == "auto" else route_hint,
        top_k=top_k,
    )

    rprint(f"[bold]Q:[/bold] {result.question}")
    rprint(
        f"[dim]route={result.route}  grounded={result.grounded}  latency={result.latency.total_ms} ms[/dim]\n"
    )
    rprint(result.answer)
    if result.citations:
        rprint("\n[bold]Citations:[/bold]")
        for c in result.citations:
            rprint(f"  - {c.path}:{c.start_line}-{c.end_line}")


def _build_cli_dense(
    *, embedder: EmbeddingProvider, sqlite_path: Path, repo: str | None
) -> DenseRetriever:
    """Pick a dense retriever for the CLI.

    If `REPOSAGE_DENSE=local` (default for CI/mock), build a `LocalDenseIndex`
    from `embeddings` rows in the local SQLite. Otherwise contact the gRPC
    server. Tests rely on the local path; production uses gRPC.
    """
    del repo  # unused for now; reserved for repo-scoped slicing
    flavour = os.environ.get("REPOSAGE_DENSE", "auto").lower()
    if flavour == "grpc":
        from reposage.retrieval.hnsw_client import HnswGrpcClient

        return HnswGrpcClient(
            expected_model=embedder.model,
            expected_dim=embedder.dim,
        )
    # auto / local: build from sqlite. Falls back trivially if there are
    # no embeddings (the dense branch then returns nothing and the hybrid
    # retriever lives off BM25 alone, which is still useful).
    from reposage.retrieval.local_dense import LocalDenseIndex
    from reposage.storage.embeddings_store import EmbeddingsStore

    idx = LocalDenseIndex(model=embedder.model, dim=embedder.dim)
    store = EmbeddingsStore(sqlite_path)
    try:
        store.init_schema()
        for ids, mat in store.iter_vectors(model=embedder.model):
            idx.add(ids, mat)
    finally:
        store.close()
    return idx


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
