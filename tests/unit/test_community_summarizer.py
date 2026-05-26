"""Unit tests for `CommunitySummarizer` (Map/Reduce + content_sha cache)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from reposage.indexer.graphrag.community import Community
from reposage.indexer.graphrag.summarizer import CommunitySummarizer
from reposage.retrieval.protocols import ChatMessage


class _CountingLLM:
    """LLM client that records call counts + returns a fixed JSON summary.

    Mirrors the duck-typed `LLMClient` Protocol but is intentionally not
    a Protocol-aware concrete class (we don't want to drag MockLLMClient
    into a unit test that needs to count calls).
    """

    def __init__(self, *, model: str = "fake-llm") -> None:
        self.calls = 0
        self.last_messages: list[ChatMessage] = []
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls += 1
        self.last_messages = list(messages)
        return '{"title": "TestModule", "summary": "Two-line auto-summary of the test module."}'


def _setup_chunks_db(tmp_path: Path) -> sqlite3.Connection:
    """A `chunks` table just barely populated enough that the Map path
    can fetch some seed chunks. We don't need real symbol-graph rows
    because the summariser's `_fetch_seed_chunks` only reads `chunks`.
    """
    conn = sqlite3.connect(tmp_path / "smoke.db")
    conn.executescript(
        """
        CREATE TABLE chunks(
          chunk_id TEXT PRIMARY KEY,
          repo TEXT, path TEXT, language TEXT,
          start_line INT, end_line INT,
          symbol TEXT, parent_symbol TEXT,
          text TEXT, file_sha TEXT, created_at INT
        );
        CREATE TABLE edges(
          src TEXT, dst TEXT, kind TEXT,
          src_path TEXT, src_line INT, weight INT DEFAULT 1
        );
        """
    )
    conn.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("c1", "r", "a.py", "python", 1, 4, "alpha", None, "def alpha():\n    pass", "sha", 0),
            ("c2", "r", "b.py", "python", 1, 4, "beta", None, "def beta():\n    pass", "sha", 0),
        ],
    )
    conn.commit()
    return conn


def _comm(
    local_id: int,
    members: tuple[str, ...],
    *,
    level: int = 0,
    content_sha: str = "abc",
    child_ids: tuple[int, ...] = (),
) -> Community:
    return Community(
        id=local_id,
        members=members,
        level=level,
        parent_id=None,
        content_sha=content_sha,
        child_ids=child_ids,
    )


def test_map_writes_title_and_summary(tmp_path: Path) -> None:
    conn = _setup_chunks_db(tmp_path)
    llm = _CountingLLM()
    summariser = CommunitySummarizer(llm)
    out = asyncio.run(
        summariser.summarize_all(
            [_comm(1, ("a.alpha", "b.beta"))],
            conn=conn,
        )
    )
    assert llm.calls == 1
    assert out[0].title == "TestModule"
    assert "Two-line" in (out[0].summary or "")
    conn.close()


def test_cache_hit_skips_llm(tmp_path: Path) -> None:
    conn = _setup_chunks_db(tmp_path)
    llm = _CountingLLM()
    summariser = CommunitySummarizer(llm)
    existing: dict[str, Community] = {
        "abc": Community(
            id=99,
            members=("a.alpha",),
            level=0,
            parent_id=None,
            content_sha="abc",
            title="Prior",
            summary="Prior summary text.",
            summary_model="prior-model",
        ),
    }
    out = asyncio.run(
        summariser.summarize_all(
            [_comm(1, ("a.alpha",), content_sha="abc")],
            conn=conn,
            existing=existing,
        )
    )
    assert llm.calls == 0
    assert out[0].title == "Prior"
    assert out[0].summary == "Prior summary text."
    conn.close()


def test_short_summary_falls_back_to_placeholder(tmp_path: Path) -> None:
    class _ShortLLM(_CountingLLM):
        async def complete(self, messages: Sequence[ChatMessage]) -> str:
            self.calls += 1
            return '{"title": "T", "summary": "tiny"}'

    conn = _setup_chunks_db(tmp_path)
    llm = _ShortLLM()
    summariser = CommunitySummarizer(llm)
    out = asyncio.run(
        summariser.summarize_all(
            [_comm(1, ("a.alpha",))],
            conn=conn,
        )
    )
    assert out[0].summary == "<auto-summary unavailable>"
    conn.close()


def test_reduce_aggregates_children(tmp_path: Path) -> None:
    conn = _setup_chunks_db(tmp_path)
    llm = _CountingLLM()
    summariser = CommunitySummarizer(llm)
    out = asyncio.run(
        summariser.summarize_all(
            [
                _comm(1, ("a.alpha",), level=0, content_sha="sha-1"),
                _comm(2, ("b.beta",), level=0, content_sha="sha-2"),
                _comm(
                    3,
                    ("a.alpha", "b.beta"),
                    level=1,
                    content_sha="sha-3",
                    child_ids=(1, 2),
                ),
            ],
            conn=conn,
        )
    )
    # 2 Map calls + 1 Reduce call.
    assert llm.calls == 3
    parent = next(c for c in out if c.id == 3)
    assert parent.summary
    assert parent.summary != "<auto-summary unavailable>"
    conn.close()


def test_failing_llm_writes_placeholder(tmp_path: Path) -> None:
    class _BrokenLLM(_CountingLLM):
        async def complete(self, messages: Sequence[ChatMessage]) -> str:
            self.calls += 1
            raise RuntimeError("upstream timeout")

    conn = _setup_chunks_db(tmp_path)
    llm = _BrokenLLM()
    summariser = CommunitySummarizer(llm)
    out = asyncio.run(summariser.summarize_all([_comm(1, ("a.alpha",))], conn=conn))
    # Hard-fail-soft: we record a placeholder but never raise out.
    assert out[0].summary == "<auto-summary unavailable>"
    conn.close()


def test_concurrency_validates(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CommunitySummarizer(_CountingLLM(), concurrency=0)
