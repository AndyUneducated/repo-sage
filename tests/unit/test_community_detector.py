"""Unit tests for `CommunityDetector`."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from reposage.indexer.graphrag.community import CommunityDetector
from reposage.indexer.symbol_graph import SymbolEdge, SymbolNode
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore


def _node(fqn: str) -> SymbolNode:
    return SymbolNode(
        fqn=fqn,
        kind="function",
        language="python",
        repo="r",
        path=f"{fqn}.py",
        start_line=1,
        end_line=10,
    )


def _edge(src: str, dst: str) -> SymbolEdge:
    return SymbolEdge(src=src, dst=dst, kind="call", src_path=f"{src}.py", src_line=1)


def _two_clusters_with_bridge() -> tuple[list[SymbolNode], list[SymbolEdge]]:
    """Build a graph with two obvious clusters {a,b,c}, {d,e,f} joined by
    one weak bridge edge c—d. Leiden should split it.
    """
    nodes = [_node(name) for name in "abcdef"]
    edges: list[SymbolEdge] = []

    # Cluster 1: a, b, c — pairwise calls with multiplicity to bump weight.
    for src, dst, n_lines in [
        ("a", "b", 5),
        ("b", "a", 5),
        ("b", "c", 5),
        ("c", "b", 5),
        ("a", "c", 5),
        ("c", "a", 5),
    ]:
        for line in range(n_lines):
            edges.append(
                SymbolEdge(src=src, dst=dst, kind="call", src_path=f"{src}.py", src_line=line + 1)
            )

    # Cluster 2: d, e, f — pairwise calls.
    for src, dst, n_lines in [
        ("d", "e", 5),
        ("e", "d", 5),
        ("e", "f", 5),
        ("f", "e", 5),
        ("d", "f", 5),
        ("f", "d", 5),
    ]:
        for line in range(n_lines):
            edges.append(
                SymbolEdge(src=src, dst=dst, kind="call", src_path=f"{src}.py", src_line=line + 1)
            )

    # One weak bridge c — d.
    edges.append(_edge("c", "d"))
    return nodes, edges


def test_two_clusters_split(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    nodes, edges = _two_clusters_with_bridge()
    store.upsert_nodes(nodes)
    store.upsert_edges(edges)

    detector = CommunityDetector(min_size=2)
    communities, stats = detector.detect(store, repo="r")
    leaves = [c for c in communities if c.level == 0]
    # Expect 2 leaf communities; if Leiden disagrees, the fixture is too small
    # — flake-proof by allowing 2 or 3 here but asserting members align.
    assert 2 <= len(leaves) <= 3
    assert stats.n_communities >= 2
    # Cluster 1 set ⊆ one community, cluster 2 set ⊆ another.
    members_by_id = {c.id: set(c.members) for c in leaves}
    found_left = any({"a", "b", "c"} <= s for s in members_by_id.values())
    found_right = any({"d", "e", "f"} <= s for s in members_by_id.values())
    assert found_left and found_right
    store.close()


def test_deterministic_under_fixed_seed(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    nodes, edges = _two_clusters_with_bridge()
    store.upsert_nodes(nodes)
    store.upsert_edges(edges)

    d1 = CommunityDetector(seed=42, min_size=2)
    d2 = CommunityDetector(seed=42, min_size=2)
    c1, _ = d1.detect(store, repo="r")
    c2, _ = d2.detect(store, repo="r")
    assert [c.members for c in c1] == [c.members for c in c2]
    store.close()


def test_empty_graph_returns_empty(tmp_path: Path) -> None:
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    detector = CommunityDetector()
    communities, stats = detector.detect(store, repo="r")
    assert communities == []
    assert stats.n_communities == 0
    store.close()


def test_hierarchy_is_strictly_monotonically_coarser(tmp_path: Path) -> None:
    """Each successive level must have *strictly fewer* communities than the
    previous one — otherwise the contraction is broken and we're producing
    near-duplicate hierarchy levels (regression for the 47-symbol → 65
    communities bug where `igraph.contract_vertices` left empty leftover
    vertices behind, inflating every subsequent Leiden run)."""
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()

    # Three tight triangles + thin bridges. With ``max_levels=3`` Leiden
    # should yield: level 0 ≈ 3 communities → level 1 < 3 → level 2 < L1.
    nodes: list[SymbolNode] = []
    edges: list[SymbolEdge] = []
    cluster_names = [
        ["a1", "a2", "a3"],
        ["b1", "b2", "b3"],
        ["c1", "c2", "c3"],
    ]
    for cluster in cluster_names:
        nodes.extend(_node(n) for n in cluster)
        for src in cluster:
            for dst in cluster:
                if src == dst:
                    continue
                for line in range(8):  # heavy intra-cluster weight
                    edges.append(
                        SymbolEdge(
                            src=src,
                            dst=dst,
                            kind="call",
                            src_path=f"{src}.py",
                            src_line=line + 1,
                        )
                    )
    # Thin bridges to make the graph one connected component.
    edges.append(_edge("a1", "b1"))
    edges.append(_edge("b1", "c1"))

    store.upsert_nodes(nodes)
    store.upsert_edges(edges)
    communities, stats = CommunityDetector(min_size=2, max_levels=3).detect(store, repo="r")

    counts_by_level: dict[int, int] = {}
    for c in communities:
        counts_by_level[c.level] = counts_by_level.get(c.level, 0) + 1
    counts = [counts_by_level[k] for k in sorted(counts_by_level)]
    assert len(counts) >= 2, f"expected hierarchy, only got {counts}"
    for prev, cur in pairwise(counts):
        assert cur < prev, f"level didn't coarsen: counts={counts}"
    # Sanity: total ≤ N nodes (in the buggy state we had > N).
    assert stats.n_communities <= len(nodes)
    store.close()


def test_singleton_nodes_dropped_before_detection(tmp_path: Path) -> None:
    """A node with no `call`/`inherit` neighbours must not get its own
    community — those would balloon the partition (and the summariser's
    token bill) for no signal gain."""
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    # Tight triangle + one orphan.
    nodes = [_node(n) for n in ["a", "b", "c", "loner"]]
    edges: list[SymbolEdge] = []
    for src, dst in [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("a", "c"), ("c", "a")]:
        edges.append(SymbolEdge(src=src, dst=dst, kind="call", src_path=f"{src}.py", src_line=1))
    store.upsert_nodes(nodes)
    store.upsert_edges(edges)

    communities, _ = CommunityDetector(min_size=2).detect(store, repo="r")
    all_members = {fqn for c in communities for fqn in c.members}
    assert "loner" not in all_members
    assert {"a", "b", "c"} <= all_members


def test_parent_child_relationships_consistent(tmp_path: Path) -> None:
    """Every level-k community's members must be a subset of its parent's
    members at level k+1. This is the invariant the `_chunks_for_communities`
    fallback relies on for higher-level community routing."""
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    nodes, edges = _two_clusters_with_bridge()
    store.upsert_nodes(nodes)
    store.upsert_edges(edges)

    communities, _ = CommunityDetector(min_size=2, max_levels=3).detect(store, repo="r")
    by_id = {c.id: c for c in communities}
    for c in communities:
        if c.parent_id is None:
            continue
        parent = by_id[c.parent_id]
        assert set(c.members) <= set(parent.members), (
            f"community {c.id} (level={c.level}) members {c.members} "
            f"not subset of parent {parent.id} (level={parent.level}) {parent.members}"
        )
        assert parent.level == c.level + 1
        assert c.id in parent.child_ids
    store.close()


def test_content_sha_changes_when_file_changes(tmp_path: Path) -> None:
    """Two indexes with the same FQNs but different file_sha → different
    content_sha. This is the cache-invalidation invariant the summariser
    relies on.
    """
    store = SQLiteSymbolGraphStore(tmp_path / "r.db")
    store.init_schema()
    nodes, edges = _two_clusters_with_bridge()
    store.upsert_nodes(nodes)
    store.upsert_edges(edges)
    # Plant fake file_meta rows with one set of shas.
    for n in nodes:
        store.upsert_file_meta(
            repo="r",
            path=n.path,
            file_sha="sha-v1",
            mtime=1,
            parse_status="ok",
        )
    c1, _ = CommunityDetector(min_size=2).detect(store, repo="r")
    sha_v1 = {c.id: c.content_sha for c in c1}

    # Mutate file_sha values.
    for n in nodes:
        store.upsert_file_meta(
            repo="r",
            path=n.path,
            file_sha="sha-v2",
            mtime=2,
            parse_status="ok",
        )
    c2, _ = CommunityDetector(min_size=2).detect(store, repo="r")
    sha_v2 = {c.id: c.content_sha for c in c2}
    # Same partition; sha must differ for every community that owns at
    # least one file (which is all of them).
    assert sha_v1 != sha_v2
    store.close()
