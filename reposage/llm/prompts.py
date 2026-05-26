"""Prompt templates kept in code (not YAML) so refactors get type-checked.

Each template explicitly forbids the LLM from inventing file paths or line
numbers — citations must come from `<retrieved_chunk>` blocks only. The
post-generation `verify_grounding` check enforces it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from reposage.retrieval.hybrid import RetrievedChunk
from reposage.retrieval.protocols import ChatMessage

ROUTER_SYSTEM = """\
You are a query classifier. Given a question about a code repository,
classify it into exactly one of:

- "graph"     : asks for a deterministic relation in the symbol graph,
                e.g. "where is X called", "what does X import", "subclasses of X".
- "community" : asks about module-level interactions or architecture,
                e.g. "how do auth and billing modules interact".
- "hybrid"    : everything else; generic semantic search over code.

Reply with a single line of valid JSON, no prose:
{"route": "graph" | "community" | "hybrid", "confidence": 0..1, "reason": "..."}
"""

ANSWER_SYSTEM = """\
You are RepoSage, a careful repository-level code Q&A assistant.

Rules:
1. Answer ONLY using facts from the <retrieved_chunk> blocks below. Never
   invent file paths or line numbers.
2. Every claim about a file or line MUST be backed by a citation that
   already appears in the context. Citations look like:
       [path/to/file.py:start-end]
   where start/end are 1-based inclusive line numbers from the chunk header.
3. If the context is insufficient, reply:
       "I do not have enough context in the index to answer."
4. Be concise: a short paragraph plus a bulleted list of locations is
   typical. Use code fences only if they are themselves cited.
"""


def render_chunk(chunk: RetrievedChunk) -> str:
    """Render one chunk as a `<retrieved_chunk>` block for the prompt."""
    header = (
        f'<retrieved_chunk path="{chunk.path}" '
        f'lines="{chunk.start_line}-{chunk.end_line}" '
        f'symbol="{chunk.symbol or ""}">'
    )
    return f"{header}\n{chunk.text}\n</retrieved_chunk>"


def build_answer_messages(
    question: str,
    chunks: Iterable[RetrievedChunk],
) -> list[ChatMessage]:
    """Compose the messages for the answering LLM call."""
    blocks = [render_chunk(c) for c in chunks]
    ctx = "\n\n".join(blocks) if blocks else "<no retrieved chunks>"
    user_content = (
        f"Question:\n{question}\n\n"
        f"Context:\n{ctx}\n\n"
        f"Answer the question following the rules. Citations must use the "
        f"format [path:start-end] and reference only the chunks above."
    )
    return [
        ChatMessage(role="system", content=ANSWER_SYSTEM),
        ChatMessage(role="user", content=user_content),
    ]


def build_router_messages(question: str) -> Sequence[ChatMessage]:
    """Compose the messages for the routing LLM call."""
    return [
        ChatMessage(role="system", content=ROUTER_SYSTEM),
        ChatMessage(role="user", content=f"Question: {question}"),
    ]


# ============================================================ GraphRAG
#
# Phase 3 adds two LLM-driven steps that need their own prompt templates:
#
# 1. **Community summary (Map)** — given a community's representative
#    members and their code, produce a short `{title, summary}` JSON
#    describing what the community does. Used at indexing time.
# 2. **Community summary (Reduce)** — given several already-summarised
#    children, produce one higher-level `{title, summary}` for a parent
#    community. Also indexing time.
# 3. **Community answer** — given a question and a set of retrieved
#    community summaries + member chunks, produce a grounded answer.
#    Used at query time. Reuses the same `[path:start-end]` citation
#    contract as the hybrid path so `verify_grounding` is unchanged.
#
# All three are JSON-out for the indexing step (so we can parse `title`
# and `summary` deterministically) and plain text + bracket citations
# for the answer step (so the post-gen verifier from Phase 2 still
# applies).


COMMUNITY_SUMMARY_SYSTEM = """\
You are a senior engineer writing module-level documentation for a
codebase. You will be shown:

- the FULL list of symbol names (functions, classes, methods) that
  belong to one module-like community of the call graph;
- a handful of representative source-code chunks for that community.

Write a short structured description of what this community does.

OUTPUT FORMAT (must be valid JSON on a single block, no prose around it):
{
  "title":   "<3-6 word noun-phrase title, e.g. 'Authentication' or 'Billing invoices'>",
  "summary": "<2-3 sentences. State the module's purpose, its main entry points, and what it depends on. Do NOT invent file paths or symbol names not in the input.>"
}

Rules:
1. Stay grounded: every claim must be derivable from the symbols /
   code shown. Do not speculate about unrelated subsystems.
2. Keep `summary` under 600 characters.
3. Do NOT include code blocks, citations, or markdown. Plain prose only.
4. If the community looks generic (utilities, helpers, glue), say so
   honestly instead of fabricating a domain meaning.
"""


COMMUNITY_REDUCE_SYSTEM = """\
You are rolling up several module summaries into one higher-level
description of a *super-module* in a codebase. You will be shown
multiple `{title, summary}` pairs for child communities; produce one
JSON object describing the union.

OUTPUT FORMAT (valid JSON, nothing else):
{
  "title":   "<3-6 word noun-phrase that captures the union>",
  "summary": "<2-3 sentences naming the major sub-modules and how they relate. Do NOT invent sub-modules that are not in the input.>"
}

Rules:
1. Mention each child by its title, but compress aggressively.
2. Keep `summary` under 600 characters.
3. No code, no markdown, no citations.
"""


COMMUNITY_ANSWER_SYSTEM = """\
You are RepoSage answering a repository-level question using
*module-aware* context (community summaries + representative code
chunks).

Rules (identical to the hybrid route, kept verbatim so verification
logic in `verify_grounding` does not have to special-case anything):

1. Use ONLY the facts in the <community> blocks and <retrieved_chunk>
   blocks below. Never invent file paths or line numbers.
2. <community> blocks summarise modules — quote their *titles* in
   prose if helpful, but do NOT cite them with bracket notation.
3. Every claim that pins something to a file MUST use a bracket
   citation [path/to/file.py:start-end] that already appears in a
   <retrieved_chunk> header above.
4. If no chunk supports a needed claim, say
   "I do not have enough context in the index to answer."
5. Prefer a short paragraph followed by a bulleted list of locations.
"""


def render_community_block(
    *,
    community_id: int | None,
    level: int,
    title: str | None,
    summary: str | None,
) -> str:
    """Render one community as a `<community>` block for prompts."""
    cid = community_id if community_id is not None else "?"
    safe_title = (title or "").replace('"', "'")
    body = summary or ""
    return f'<community id="{cid}" level="{level}" title="{safe_title}">\n{body}\n</community>'


def build_community_summary_messages(
    *,
    members: Iterable[str],
    seeds: Iterable[str],
    seed_chunks: Iterable[tuple[str, str, int, int, str]],
    level: int,
) -> list[ChatMessage]:
    """Map-phase prompt for one leaf community.

    `seed_chunks` rows are ``(path, symbol, start_line, end_line, text)``.
    We deliberately *omit* the FQN list for very large communities (>100
    members) to keep the prompt small; the seed symbol list is the
    important signal.
    """
    members_list = list(members)
    seeds_list = list(seeds)
    chunks_list = list(seed_chunks)
    n_members = len(members_list)

    # Member list is purely for context; cap to keep prompt size sane.
    member_str = (
        ", ".join(members_list)
        if n_members <= 100
        else f"{', '.join(members_list[:100])} ... (+{n_members - 100} more)"
    )

    chunk_blocks = "\n\n".join(
        f'<chunk path="{path}" symbol="{symbol}" lines="{start}-{end}">\n{text}\n</chunk>'
        for path, symbol, start, end, text in chunks_list
    )

    user_content = (
        f"Community level: {level}\n"
        f"Members ({n_members}): {member_str}\n"
        f"Seed members: {', '.join(seeds_list) if seeds_list else '<none>'}\n\n"
        f"Representative code:\n"
        f"{chunk_blocks if chunk_blocks else '<no code chunks available>'}\n\n"
        f"Produce the JSON object as instructed."
    )
    return [
        ChatMessage(role="system", content=COMMUNITY_SUMMARY_SYSTEM),
        ChatMessage(role="user", content=user_content),
    ]


def build_community_reduce_messages(
    *,
    child_summaries: Iterable[tuple[str | None, str]],
    level: int,
) -> list[ChatMessage]:
    """Reduce-phase prompt for one parent community.

    `child_summaries` is an iterable of `(title, summary)` tuples; we
    just enumerate them in the prompt.
    """
    children = list(child_summaries)
    if not children:
        raise ValueError("at least one child summary is required for reduce")
    body = "\n\n".join(
        f"Child {i + 1}:\n  title: {title or 'Untitled'}\n  summary: {summary}"
        for i, (title, summary) in enumerate(children)
    )
    user_content = (
        f"Parent community level: {level}\n"
        f"Number of children: {len(children)}\n\n"
        f"{body}\n\n"
        f"Produce the JSON object as instructed."
    )
    return [
        ChatMessage(role="system", content=COMMUNITY_REDUCE_SYSTEM),
        ChatMessage(role="user", content=user_content),
    ]


def build_community_answer_messages(
    question: str,
    *,
    communities: Iterable[tuple[int, int, str | None, str | None]],
    chunks: Iterable[RetrievedChunk],
) -> list[ChatMessage]:
    """Online answer prompt for the community route.

    `communities` rows are ``(community_id, level, title, summary)``.
    We render them as `<community>` blocks; `chunks` are rendered as
    `<retrieved_chunk>` blocks (same format as Phase 2 so the
    grounding verifier doesn't need a new code path).
    """
    community_blocks = [
        render_community_block(community_id=cid, level=level, title=title, summary=summary)
        for cid, level, title, summary in communities
    ]
    chunk_blocks = [render_chunk(c) for c in chunks]

    ctx_parts: list[str] = []
    if community_blocks:
        ctx_parts.append("Community summaries:\n" + "\n\n".join(community_blocks))
    if chunk_blocks:
        ctx_parts.append("Retrieved code:\n" + "\n\n".join(chunk_blocks))
    ctx = "\n\n".join(ctx_parts) if ctx_parts else "<no context>"

    user_content = (
        f"Question:\n{question}\n\n"
        f"Context:\n{ctx}\n\n"
        f"Answer the question following the rules. Citations must use the "
        f"format [path:start-end] and reference only the <retrieved_chunk> "
        f"blocks above."
    )
    return [
        ChatMessage(role="system", content=COMMUNITY_ANSWER_SYSTEM),
        ChatMessage(role="user", content=user_content),
    ]
