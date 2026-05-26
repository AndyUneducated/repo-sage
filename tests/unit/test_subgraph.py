"""Unit tests for `reposage.indexer.graphrag.subgraph`."""

from __future__ import annotations

from pathlib import Path

import pytest
from reposage.indexer.graphrag.subgraph import build_igraph
from reposage.indexer.symbol_graph import SymbolEdge, SymbolNode
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore


def _make_store(tmp_path: Path) -> SQLiteSymbolGraphStore:
    store = SQLiteSymbolGraphStore(tmp_path / "repo.db")
    store.init_schema()
    return store


def _node(fqn: str) -> SymbolNode:
    return SymbolNode(
        fqn=fqn,
        kind="function",
        language="python",
        repo="r",
        path=f"{fqn}.py",
        start_line=1,
        end_line=2,
    )


def _edge(src: str, dst: str, kind: str = "call") -> SymbolEdge:
    return SymbolEdge(src=src, dst=dst, kind=kind, src_path=f"{src}.py", src_line=1)


def test_symmetrise_sums_directed_weights(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a"), _node("b")])
    # Three a→b edges + one b→a edge, all kind=call. Phase 1 edge writer
    # accumulates `weight` so duplicate (src,dst,kind,src_line) collapse.
    store.upsert_edges(
        [
            _edge("a", "b"),
            SymbolEdge(src="a", dst="b", kind="call", src_path="a.py", src_line=2),
            SymbolEdge(src="a", dst="b", kind="call", src_path="a.py", src_line=3),
            _edge("b", "a"),
        ]
    )

    g, stats = build_igraph(store, repo="r")
    assert stats.n_vertices == 2
    assert stats.n_edges == 1  # one undirected pair (a, b)
    # `weight` should be the sum of both directions.
    assert g.es["weight"][0] == pytest.approx(3 + 1)
    store.close()


def test_unresolved_destinations_are_dropped(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a")])
    store.upsert_edges(
        [
            _edge("a", "<unresolved:foo>"),
            SymbolEdge(src="<unresolved:bar>", dst="a", kind="call", src_path="x.py", src_line=1),
        ]
    )
    g, stats = build_igraph(store, repo="r")
    assert g.ecount() == 0
    assert stats.n_dropped_unresolved == 2
    store.close()


def test_import_edges_excluded_by_default(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a"), _node("b")])
    store.upsert_edges([_edge("a", "b", kind="import")])
    g, _ = build_igraph(store, repo="r")
    assert g.ecount() == 0
    store.close()


def test_inherit_edges_included(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a"), _node("b")])
    store.upsert_edges([_edge("a", "b", kind="inherit")])
    g, _ = build_igraph(store, repo="r")
    assert g.ecount() == 1
    store.close()


def test_self_loops_dropped(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a")])
    store.upsert_edges([_edge("a", "a")])
    g, _ = build_igraph(store, repo="r")
    assert g.ecount() == 0
    store.close()


def test_vertices_carry_fqn_and_language(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("alpha"), _node("beta")])
    # At least one resolved edge so the vertices are not pruned as isolated.
    store.upsert_edges([_edge("alpha", "beta")])
    g, _ = build_igraph(store, repo="r")
    assert sorted(g.vs["fqn"]) == ["alpha", "beta"]
    assert all(lang == "python" for lang in g.vs["language"])
    store.close()


def test_empty_repo_returns_empty_graph(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    g, stats = build_igraph(store, repo="r")
    assert g.vcount() == 0
    assert g.ecount() == 0
    assert stats.n_vertices == 0
    store.close()


def test_isolated_nodes_are_pruned_from_subgraph(tmp_path: Path) -> None:
    """Regression: pre-fix, module-level nodes with no call/inherit edge
    leaked into the partition as singleton communities (in the demo,
    47 nodes → 65 communities). The subgraph builder must drop them so
    they don't pollute Leiden's input."""
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("connected_a"), _node("connected_b"), _node("orphan")])
    store.upsert_edges([_edge("connected_a", "connected_b")])
    g, stats = build_igraph(store, repo="r")
    assert g.vcount() == 2
    assert stats.n_dropped_isolated == 1
    assert sorted(g.vs["fqn"]) == ["connected_a", "connected_b"]
    store.close()


def test_node_isolated_only_via_excluded_edge_is_dropped(tmp_path: Path) -> None:
    """A node whose only edge is an `import` (excluded) edge should still
    be considered isolated and pruned — `import` graphs would otherwise
    smuggle most module-level FQNs into the partition."""
    store = _make_store(tmp_path)
    store.upsert_nodes([_node("a"), _node("b"), _node("c")])
    store.upsert_edges(
        [
            _edge("a", "b", kind="import"),  # excluded
            _edge("a", "c", kind="call"),
        ]
    )
    g, stats = build_igraph(store, repo="r")
    assert g.vcount() == 2
    # 'b' is the only one fully isolated by edge-kind filtering.
    assert "b" not in set(g.vs["fqn"])
    assert stats.n_dropped_isolated == 1
    store.close()
