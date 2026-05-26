"""Leiden community detection on the symbol graph (Phase 3).

Why Leiden over Louvain: Leiden guarantees well-connected communities (no
"badly connected" partitions) and yields more stable partitions when the
graph is iteratively re-indexed (Traag, Waltman, van Eck 2019).

The detector produces a *hierarchical* partition:

* Level 0 — base Leiden on the original graph.
* Level k>0 — Leiden re-run on a contracted graph where each level-k-1
  community becomes one vertex; edges between communities get summed
  weights. This matches the Microsoft GraphRAG layered structure
  (Edge et al. 2024) without us having to implement their bespoke
  hierarchical routine.

Output `Community.id` is a detection-local sequential int starting at 1.
`CommunityStore.upsert` is responsible for mapping these local ids to the
SQLite `community_id` autoincrement column and rewriting `parent_id`
references.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from reposage.indexer.graphrag.subgraph import SubgraphStats, build_igraph
from reposage.storage.sqlite_graph import SQLiteSymbolGraphStore

if TYPE_CHECKING:
    import igraph

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Community:
    """One community at one hierarchy level.

    The detection pipeline produces these with `summary=None` /
    `title=None`; the `CommunitySummarizer` returns a new `Community`
    with those fields populated via `dataclasses.replace`.
    """

    id: int  # detection-local id (1-based)
    members: tuple[str, ...]  # symbol FQNs (sorted, deterministic)
    level: int  # 0 = leaf (finest), 1+ = coarser
    parent_id: int | None
    content_sha: str = ""  # sha256 of members + their file_sha
    title: str | None = None
    summary: str | None = None
    summary_model: str | None = None
    # Children's *local* ids; populated for non-leaf levels. Handy for the
    # summarizer's Reduce step so it doesn't need a second SQL query.
    child_ids: tuple[int, ...] = field(default_factory=tuple)

    @property
    def is_leaf(self) -> bool:
        return self.level == 0


@dataclass(slots=True, frozen=True)
class DetectionStats:
    """One-call summary; carried up to `IndexManifest` in Phase 3."""

    subgraph: SubgraphStats
    n_communities: int
    n_levels: int
    n_small_merged: int


class CommunityDetector:
    """Run hierarchical Leiden against the symbol graph.

    Parameters mirror `leidenalg.find_partition` knobs; defaults match
    `Settings.community_*` (see `reposage/config.py`).
    """

    def __init__(
        self,
        resolution: float = 1.0,
        max_levels: int = 3,
        min_size: int = 3,
        seed: int = 1337,
    ) -> None:
        if max_levels < 1:
            raise ValueError("max_levels must be >= 1")
        if min_size < 1:
            raise ValueError("min_size must be >= 1")
        self.resolution = resolution
        self.max_levels = max_levels
        self.min_size = min_size
        self.seed = seed

    # ------------------------------------------------------------------ API

    def detect(
        self,
        store: SQLiteSymbolGraphStore,
        *,
        repo: str,
    ) -> tuple[list[Community], DetectionStats]:
        """Run hierarchical Leiden and return level-0..N communities.

        `content_sha` is per-community: the sha is computed from the
        community's sorted FQNs *and* the `file_sha` of every file those
        FQNs live in — so an edit to one file only invalidates the
        communities that own symbols in that file.
        """
        graph, sub_stats = build_igraph(store, repo=repo)
        if graph.vcount() == 0:
            return [], DetectionStats(
                subgraph=sub_stats, n_communities=0, n_levels=0, n_small_merged=0
            )

        path_by_fqn = self._fqn_to_path(store, repo=repo)
        file_sha_by_path = self._path_to_sha(store, repo=repo)

        # ---- Level 0: Leiden on the original graph ----
        level0 = self._leiden(graph)
        level0, n_small = self._merge_small(graph, level0)
        # Densify so the cluster-id space is exactly 0..K-1 in
        # first-appearance order — keeps `_groups_from_partition` and
        # `_contract` perfectly in step on the contracted vertex ids.
        level0 = self._densify(level0)

        # Convert level0 cluster ids → FQN groups (sorted).
        level0_groups = self._groups_from_partition(graph, level0)

        # ---- Level 1..N: iteratively contract + re-cluster ----
        # Each level is a list of FQN tuples (one tuple per community).
        levels: list[list[tuple[str, ...]]] = [level0_groups]
        contracted = graph
        cluster_assignment = level0  # vertex index → cluster id at this level
        # We'll need a mapping from each child-level cluster to its parent
        # cluster at the next level. We track this as we go.
        for _ in range(1, self.max_levels):
            contracted, parent_assignment = self._contract(contracted, cluster_assignment)
            if contracted.vcount() <= 1:
                break
            next_partition = self._densify(self._leiden(contracted))
            # Map current-level community → vertex in next contracted graph
            # is straightforward (1:1 by index). next_partition gives the
            # next-level cluster id for each current-level community.
            #
            # We collapse one level: build groups of *original FQNs* for
            # the new level by unioning all child-level groups that share
            # a next-level cluster id.
            child_to_parent: dict[int, int] = dict(enumerate(next_partition))
            del parent_assignment  # unused; child_to_parent is what we need
            children = levels[-1]
            buckets: dict[int, list[str]] = defaultdict(list)
            for child_idx, fqns in enumerate(children):
                parent_cluster = child_to_parent[child_idx]
                buckets[parent_cluster].extend(fqns)
            new_groups = [tuple(sorted(fqns)) for fqns in buckets.values()]
            # Stop if the level didn't actually coarsen.
            if len(new_groups) >= len(children):
                break
            levels.append(new_groups)
            cluster_assignment = next_partition

        return self._assemble(levels, path_by_fqn, file_sha_by_path), DetectionStats(
            subgraph=sub_stats,
            n_communities=sum(len(lvl) for lvl in levels),
            n_levels=len(levels),
            n_small_merged=n_small,
        )

    # ----------------------------------------------------------- internals

    def _leiden(self, g: igraph.Graph) -> list[int]:
        """Run one pass of Leiden; return a per-vertex cluster id list.

        We use ``RBConfigurationVertexPartition`` because its
        ``resolution_parameter`` lets us nudge granularity without
        retraining anything. Modularity-only would lock granularity to
        the size of the graph.
        """
        import leidenalg as la  # noqa: PLC0415

        weights = g.es["weight"] if g.ecount() > 0 else None
        partition = la.find_partition(
            g,
            la.RBConfigurationVertexPartition,
            weights=weights,
            resolution_parameter=self.resolution,
            seed=self.seed,
        )
        return list(partition.membership)

    def _merge_small(self, g: igraph.Graph, membership: list[int]) -> tuple[list[int], int]:
        """Re-assign vertices in undersized communities to their strongest
        neighbour community.

        Strategy: for every community below ``min_size``, look at each
        member vertex, sum incident edge weights to every other
        community, and reassign the vertex to the community with the
        highest total weight. Truly isolated communities (no inter-edges
        at all) are left as-is — they're rare and removing them would
        require dropping their nodes.
        """
        if g.vcount() == 0:
            return membership, 0
        sizes: dict[int, int] = defaultdict(int)
        for c in membership:
            sizes[c] += 1
        small_clusters = {c for c, n in sizes.items() if n < self.min_size}
        if not small_clusters:
            return membership, 0
        new_membership = list(membership)
        reassigned = 0
        for v in range(g.vcount()):
            if new_membership[v] not in small_clusters:
                continue
            scores: dict[int, float] = defaultdict(float)
            for nb in g.neighbors(v):
                target = new_membership[nb]
                if target in small_clusters and target != new_membership[v]:
                    # Don't migrate from one undersized community into
                    # another; only "absorb into a big one" is allowed.
                    continue
                if target == new_membership[v]:
                    continue
                eid = g.get_eid(v, nb, error=False)
                w = float(g.es[eid]["weight"]) if eid >= 0 else 1.0
                scores[target] += w
            if not scores:
                continue
            best = max(scores.items(), key=lambda kv: kv[1])[0]
            new_membership[v] = best
            reassigned += 1
        return new_membership, reassigned

    def _contract(self, g: igraph.Graph, membership: list[int]) -> tuple[igraph.Graph, list[int]]:
        """Build a contracted graph: one vertex per cluster, summed edge
        weights between clusters.

        We *densify* the membership first — `igraph.Graph.contract_vertices`
        keeps positional vertex ids, so gaps in the membership numbering
        leave behind empty leftover vertices, which Leiden then treats
        as their own communities (level after level), bloating the
        partition. Renumbering 0..K-1 makes the contracted graph
        K-vertex exactly.

        Returns ``(contracted, dense_membership)``. ``dense_membership[i]``
        is vertex ``i``'s position in the contracted graph (i.e. its
        next-level cluster id), so callers can use it to map child-level
        ids to parent-level ids.
        """
        # Build a canonical 0..K-1 renumbering, preserving the order in
        # which cluster ids first appear. This is what `_groups_from_partition`
        # also uses, so positions in the children list line up with
        # vertex ids in the contracted graph.
        seen: dict[int, int] = {}
        dense = [seen.setdefault(c, len(seen)) for c in membership]

        contracted = g.copy()
        contracted.contract_vertices(dense, combine_attrs=None)
        # Simplify: collapse multi-edges by summing weight, drop self-loops.
        contracted.simplify(multiple=True, loops=True, combine_edges={"weight": "sum"})
        return contracted, dense

    @staticmethod
    def _densify(membership: list[int]) -> list[int]:
        """Renumber cluster ids 0..K-1 in first-appearance order.

        Idempotent: a membership already in 0..K-1 form is returned with
        the same values. Used after `_merge_small` (which can leave
        gaps) and after `_leiden` on the contracted graph (defensive —
        leidenalg normally returns dense ids).
        """
        seen: dict[int, int] = {}
        return [seen.setdefault(c, len(seen)) for c in membership]

    def _groups_from_partition(
        self, g: igraph.Graph, membership: list[int]
    ) -> list[tuple[str, ...]]:
        """Map ``membership`` → list of sorted FQN tuples (one per cluster)."""
        fqns: list[str] = g.vs["fqn"]
        buckets: dict[int, list[str]] = defaultdict(list)
        for v, c in enumerate(membership):
            buckets[c].append(fqns[v])
        # Sort cluster ids so two runs of the detector at the same seed
        # produce identical Community.id ordering.
        return [tuple(sorted(buckets[c])) for c in sorted(buckets.keys())]

    def _assemble(
        self,
        levels: list[list[tuple[str, ...]]],
        path_by_fqn: dict[str, str],
        file_sha_by_path: dict[str, str],
    ) -> list[Community]:
        """Flatten per-level FQN groups into a ``list[Community]`` with
        parent / child links.

        Local ids are 1-based and assigned level-by-level: level 0 first,
        then level 1, etc. ``parent_id`` is found by checking which
        level-(k+1) group contains all FQNs of a level-k group.
        """
        if not levels:
            return []
        out: list[Community] = []
        next_id = 1
        # Per-level: a list of (local_id, fqn_set) so we can stitch parents.
        per_level: list[list[tuple[int, set[str]]]] = []
        for level_idx, groups in enumerate(levels):
            level_rows: list[tuple[int, set[str]]] = []
            for members in groups:
                local_id = next_id
                next_id += 1
                level_rows.append((local_id, set(members)))
                out.append(
                    Community(
                        id=local_id,
                        members=members,
                        level=level_idx,
                        parent_id=None,  # filled below
                        content_sha=self._content_sha(members, path_by_fqn, file_sha_by_path),
                    )
                )
            per_level.append(level_rows)

        # Stitch parent_id and child_ids: a level-k community whose member
        # set is a subset of a level-(k+1) community is a child of that
        # parent. Since contraction produces strict containment, set
        # subset matches one and only one parent.
        for k in range(len(per_level) - 1):
            parents = per_level[k + 1]
            children = per_level[k]
            for child_local_id, child_set in children:
                parent_match = next(
                    (pid for pid, pset in parents if child_set <= pset),
                    None,
                )
                if parent_match is None:
                    continue
                # Mutate via dataclasses.replace because Community is frozen.
                child_idx = self._find_idx(out, child_local_id)
                out[child_idx] = replace(out[child_idx], parent_id=parent_match)
                parent_idx = self._find_idx(out, parent_match)
                parent = out[parent_idx]
                out[parent_idx] = replace(
                    parent,
                    child_ids=(*parent.child_ids, child_local_id),
                )
        return out

    @staticmethod
    def _find_idx(communities: list[Community], local_id: int) -> int:
        for i, c in enumerate(communities):
            if c.id == local_id:
                return i
        raise KeyError(f"community local id {local_id} not in list")

    @staticmethod
    def _content_sha(
        members: tuple[str, ...],
        path_by_fqn: dict[str, str],
        file_sha_by_path: dict[str, str],
    ) -> str:
        """sha256 over sorted FQNs + (sorted unique) file_shas of the files
        the members live in.

        Stable across re-indexing as long as neither the membership nor
        any owning file's content changes — the precise invariant the
        summarizer's cache needs.
        """
        h = hashlib.sha256()
        for fqn in members:
            h.update(b"\x00fqn:")
            h.update(fqn.encode("utf-8"))
        paths = sorted({path_by_fqn.get(fqn, "") for fqn in members})
        for path in paths:
            sha = file_sha_by_path.get(path, "")
            h.update(b"\x00path:")
            h.update(path.encode("utf-8"))
            h.update(b"\x00sha:")
            h.update(sha.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _fqn_to_path(store: SQLiteSymbolGraphStore, *, repo: str) -> dict[str, str]:
        conn = store._connect()
        rows = conn.execute("SELECT fqn, path FROM nodes WHERE repo = ?", (repo,)).fetchall()
        return {fqn: path for fqn, path in rows}

    @staticmethod
    def _path_to_sha(store: SQLiteSymbolGraphStore, *, repo: str) -> dict[str, str]:
        conn = store._connect()
        rows = conn.execute(
            "SELECT path, file_sha FROM file_meta WHERE repo = ?", (repo,)
        ).fetchall()
        return {path: sha for path, sha in rows}
