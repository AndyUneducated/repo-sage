"""Unit tests for the Phase 3 community prompts."""

from __future__ import annotations

from pathlib import Path

import pytest
from reposage.llm.prompts import (
    build_community_answer_messages,
    build_community_reduce_messages,
    build_community_summary_messages,
)
from reposage.retrieval.hybrid import RetrievedChunk


def _chunk(path: str, start: int, end: int, text: str = "x = 1\n") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="cid",
        repo="r",
        path=Path(path),
        start_line=start,
        end_line=end,
        text=text,
        symbol="alpha",
        score=1.0,
        source="rrf",
    )


def test_summary_prompt_mentions_seeds_and_chunks() -> None:
    msgs = build_community_summary_messages(
        members=("a.alpha", "b.beta"),
        seeds=("a.alpha",),
        seed_chunks=[("a.py", "alpha", 1, 4, "def alpha():\n    pass")],
        level=0,
    )
    body = msgs[1].content
    assert "a.alpha" in body
    assert "def alpha" in body
    # Summariser system must demand JSON output.
    assert "JSON" in msgs[0].content


def test_reduce_prompt_rejects_empty_children() -> None:
    with pytest.raises(ValueError):
        build_community_reduce_messages(child_summaries=[], level=1)


def test_reduce_prompt_lists_children() -> None:
    msgs = build_community_reduce_messages(
        child_summaries=[
            ("Auth", "Authentication module."),
            ("Billing", "Payments module."),
        ],
        level=1,
    )
    body = msgs[1].content
    assert "Auth" in body and "Billing" in body


def test_answer_prompt_keeps_retrieved_chunk_format() -> None:
    """The community-route answer prompt must still emit <retrieved_chunk>
    blocks so the Phase 2 grounding verifier works unchanged."""
    msgs = build_community_answer_messages(
        question="how do auth and billing interact?",
        communities=[(1, 0, "Auth", "auth summary"), (2, 0, "Billing", "billing summary")],
        chunks=[_chunk("auth/login.py", 10, 20, "def login(): ...")],
    )
    body = msgs[1].content
    assert "<community" in body
    assert "<retrieved_chunk" in body
    # Citations format clause is in the system or user content.
    combined = msgs[0].content + msgs[1].content
    assert "[path:start-end]" in combined


def test_answer_prompt_handles_no_chunks() -> None:
    msgs = build_community_answer_messages(
        question="?",
        communities=[(1, 0, "Auth", "auth summary")],
        chunks=[],
    )
    # We still produce a well-formed prompt — the LLM will then return
    # the "no context" sentinel.
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
