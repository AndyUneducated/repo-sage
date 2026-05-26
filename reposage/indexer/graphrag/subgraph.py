"""Build an `igraph.Graph` from the SQLite symbol graph for Leiden detection.

Why we filter:

* `import` edges are *too* dense — every utility module gets pulled in by
  most files, and Leiden collapses everything into one giant community.
  `call` + `inherit` captures the actual coupling we want to cluster on
  (Microsoft GraphRAG also restricts to "relationship" edges, not co-occurrence).
* `<unresolved:*>` destinations are placeholders for names we could not
  resolve to a real FQN. They have no node row, so feeding them to igraph
  would either raise or create phantom vertices.
* Leiden's optimiser assumes an *undirected* graph. We symmetrise by summing
  the weights of `a→b` and `b→a` into one edge so both directions of
  coupling reinforce each other.

The returned `igraph.Graph` carries:

* vertex attribute ``fqn``  — the canonical symbol name (used to write
  `community_members` rows back to SQLite later).
* vertex attribute ``language`` — handy for Phase 7 multi-language stats.
* edge   attribute ``weight`` — the symmetrised edge weight; Leiden uses
  this when scoring partitions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

if TYPE_CHECKING:
    import igraph

DEFAULT_EDGE_KINDS: tuple[str, ...] = ("call", "inherit")
UNRESOLVED_PREFIX = "<unresolved:"


@dataclass(slots=True, frozen=True)
class SubgraphStats:
    """Summary of one `build_igraph` call — handy for indexer manifests."""

    n_vertices: int
    n_edges: int
    n_dropped_unresolved: int
    n_collapsed_pairs: int  # how many (a→b, b→a) pairs we folded into one
    n_dropped_isolated: int  # nodes filtered for having zero call/inherit edges
    edge_kinds: tuple[str, ...]


def build_igraph(
    store: SQLiteSymbolGraphStore,
    *,
    repo: str,
    edge_kinds: Iterable[str] = DEFAULT_EDGE_KINDS,
) -> tuple[igraph.Graph, SubgraphStats]:
    """Build an undirected, weighted `igraph.Graph` for one repo.

    Returns the graph plus a stats dataclass. Vertices are ordered by
    SQLite's natural ``nodes`` ordering; we map FQN → integer index in the
    process so Leiden's vertex ids round-trip cleanly back to FQNs.
    """
    import igraph as ig  # noqa: PLC0415 — heavy import, deferred

    conn = store._connect()
    kinds = tuple(edge_kinds)
    if not kinds:
        raise ValueError("edge_kinds must be non-empty")

    # Vertices: every node row for the repo, ordered by FQN so the layout
    # is deterministic across runs (helps Leiden seed reproducibility).
    node_rows = conn.execute(
        "SELECT fqn, language FROM nodes WHERE repo = ? ORDER BY fqn",
        (repo,),
    ).fetchall()
    fqn_to_idx: dict[str, int] = {fqn: i for i, (fqn, _) in enumerate(node_rows)}
    fqns: list[str] = [r[0] for r in node_rows]
    langs: list[str] = [r[1] for r in node_rows]

    # Edges: pull (src, dst, weight) for the allowed kinds, then symmetrise
    # and fold (a,b) ≡ (b,a) into one bag with summed weight.
    placeholders = ",".join("?" * len(kinds))
    edge_rows = conn.execute(
        f"SELECT src, dst, weight FROM edges WHERE kind IN ({placeholders})",
        kinds,
    ).fetchall()

    bag: dict[tuple[int, int], float] = defaultdict(float)
    n_dropped = 0
    raw_directed = 0
    for src, dst, weight in edge_rows:
        if src.startswith(UNRESOLVED_PREFIX) or dst.startswith(UNRESOLVED_PREFIX):
            n_dropped += 1
            continue
        si = fqn_to_idx.get(src)
        di = fqn_to_idx.get(dst)
        if si is None or di is None:
            # Edge points outside this repo's node set (e.g. cross-repo
            # import — kept for symmetry, but cannot cluster on it).
            n_dropped += 1
            continue
        if si == di:
            # Self-loops carry no community-detection signal and would
            # bias modularity scoring. Drop silently.
            continue
        key = (si, di) if si < di else (di, si)
        bag[key] += float(weight)
        raw_directed += 1

    edges = list(bag.keys())
    weights = [bag[k] for k in edges]
    n_collapsed = raw_directed - len(edges)

    # Drop isolated nodes (no call/inherit edge in either direction).
    # Such nodes — typically module-level FQNs whose only relationships
    # are `import` edges (excluded above) — would otherwise become
    # singleton communities that pollute the partition and inflate the
    # downstream summariser's LLM bill. They remain in the `nodes`
    # table so hybrid retrieval can still surface them.
    referenced: set[int] = set()
    for s, d in edges:
        referenced.add(s)
        referenced.add(d)
    kept_indices = sorted(referenced)
    n_dropped_isolated = len(fqns) - len(kept_indices)
    old_to_new = {old: new for new, old in enumerate(kept_indices)}
    new_edges = [(old_to_new[s], old_to_new[d]) for s, d in edges]
    new_fqns = [fqns[i] for i in kept_indices]
    new_langs = [langs[i] for i in kept_indices]

    g = ig.Graph(n=len(new_fqns), edges=new_edges, directed=False)
    g.vs["fqn"] = new_fqns
    g.vs["language"] = new_langs
    g.es["weight"] = weights

    stats = SubgraphStats(
        n_vertices=len(new_fqns),
        n_edges=len(new_edges),
        n_dropped_unresolved=n_dropped,
        n_collapsed_pairs=max(0, n_collapsed),
        n_dropped_isolated=n_dropped_isolated,
        edge_kinds=kinds,
    )
    return g, stats
