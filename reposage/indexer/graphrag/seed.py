"""Pick representative ("seed") FQNs from a community to feed the
summariser's Map prompt.

Two cheap signals — neither requires an extra graph traversal:

* **In-degree** (how many other symbols call this one): high in-degree
  → public-facing API of the community. We pull it from SQLite with a
  single `GROUP BY` over `edges(dst, kind='call')`.
* **Chunk size** (lines of code in the symbol's chunk): bigger chunks
  carry more contextual signal per LLM token.

Score is a weighted sum; ties are broken by FQN to keep the choice
deterministic across runs.

We do *not* try to embed-and-cluster within a community here — that
would just push more compute into indexing. Top-K by structural signal
is the same heuristic Microsoft GraphRAG uses for "entity selection"
inside a community.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from reposage.indexer.graphrag.community import Community


@dataclass(slots=True, frozen=True)
class SeedCandidate:
    fqn: str
    in_degree: int
    chunk_lines: int
    score: float


# Weights are deliberately simple integers so the score is easy to debug
# in the SQLite REPL. Tweaking these without a benchmark is discouraged.
_W_IN_DEGREE = 3.0
_W_CHUNK_LINES = 1.0


def pick_seed_members(
    community: Community,
    *,
    conn: sqlite3.Connection,
    max_seeds: int = 12,
) -> list[SeedCandidate]:
    """Return up to ``max_seeds`` FQNs scored for "good summary input".

    ``conn`` must be a connection to the same SQLite database as the
    symbol graph / chunk store. We do *not* open a new connection here
    because the caller (`CommunitySummarizer`) needs to keep its
    transaction boundaries explicit.
    """
    if not community.members:
        return []
    if max_seeds <= 0:
        raise ValueError("max_seeds must be > 0")

    placeholders = ",".join("?" * len(community.members))
    members = tuple(community.members)

    # In-degree: how many CALL edges *land on* this FQN. We don't
    # restrict to within-community callers — popularity at the repo
    # level is what we want as a relevance signal.
    in_deg_rows = conn.execute(
        f"SELECT dst, COUNT(*) FROM edges "
        f"WHERE kind = 'call' AND dst IN ({placeholders}) GROUP BY dst",
        members,
    ).fetchall()
    in_deg: dict[str, int] = {fqn: int(n) for fqn, n in in_deg_rows}

    # Chunk lines: the longest chunk owned by each member (a class may
    # span multiple chunks; we take the max so we represent the symbol
    # at its richest).
    chunk_rows = conn.execute(
        f"SELECT symbol, MAX(end_line - start_line + 1) FROM chunks "
        f"WHERE symbol IN ({placeholders}) GROUP BY symbol",
        members,
    ).fetchall()
    chunk_lines: dict[str, int] = {sym: int(n) for sym, n in chunk_rows}

    # The `symbol` column on `chunks` is the bare name (e.g. `login`),
    # not the FQN. So we also try matching by the FQN's leaf segment.
    leaf_index = {fqn: fqn.rsplit(".", 1)[-1] for fqn in members}

    candidates: list[SeedCandidate] = []
    for fqn in members:
        d = in_deg.get(fqn, 0)
        lines = chunk_lines.get(leaf_index[fqn], 0)
        score = _W_IN_DEGREE * d + _W_CHUNK_LINES * (lines / 10.0)
        candidates.append(SeedCandidate(fqn=fqn, in_degree=d, chunk_lines=lines, score=score))

    candidates.sort(key=lambda c: (-c.score, c.fqn))
    return candidates[:max_seeds]


def fqns_only(candidates: Iterable[SeedCandidate]) -> list[str]:
    """Convenience: extract just the FQN strings."""
    return [c.fqn for c in candidates]
