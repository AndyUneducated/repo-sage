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
