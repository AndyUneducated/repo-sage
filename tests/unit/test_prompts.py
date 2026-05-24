"""Unit tests for prompt templates."""

from __future__ import annotations

from pathlib import Path

from reposage.llm.prompts import (
    ANSWER_SYSTEM,
    ROUTER_SYSTEM,
    build_answer_messages,
    build_router_messages,
    render_chunk,
)
from reposage.retrieval.hybrid import RetrievedChunk


def _chunk(text: str = "x = 1\n") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        repo="r",
        path=Path("a/b.py"),
        start_line=10,
        end_line=12,
        text=text,
        symbol="foo",
        score=0.5,
        source="rrf",
    )


def test_render_chunk_includes_path_and_lines() -> None:
    block = render_chunk(_chunk())
    assert "<retrieved_chunk" in block
    assert 'path="a/b.py"' in block
    assert 'lines="10-12"' in block
    assert "</retrieved_chunk>" in block


def test_render_chunk_includes_text() -> None:
    block = render_chunk(_chunk(text="def foo():\n    return 1\n"))
    assert "def foo()" in block


def test_build_answer_messages_has_system_then_user() -> None:
    msgs = build_answer_messages("how?", [_chunk()])
    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[0].content == ANSWER_SYSTEM
    assert "how?" in msgs[1].content
    assert "<retrieved_chunk" in msgs[1].content


def test_build_answer_messages_no_chunks() -> None:
    msgs = build_answer_messages("how?", [])
    assert "<no retrieved chunks>" in msgs[1].content


def test_build_router_messages_uses_router_system() -> None:
    msgs = list(build_router_messages("explain auth"))
    assert msgs[0].content == ROUTER_SYSTEM
    assert msgs[1].role == "user"


def test_answer_system_explicit_about_citations() -> None:
    """The whole point of Phase 2 is that the prompt forbids fabrication."""
    assert "[path/to/file" in ANSWER_SYSTEM or "[path:start-end]" in ANSWER_SYSTEM
    # Match across the line wrap: the prompt says "Never\n   invent ...".
    normalised = " ".join(ANSWER_SYSTEM.split())
    assert "Never invent" in normalised
