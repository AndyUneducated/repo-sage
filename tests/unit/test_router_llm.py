"""Tests for the Phase 2 LLM-backed router fallback."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from reposage.retrieval.protocols import ChatMessage
from reposage.retrieval.router import QueryRouter


class FakeLLM:
    def __init__(self, response: str, model: str = "fake") -> None:
        self._response = response
        self._model = model
        self.calls: list[Sequence[ChatMessage]] = []

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        return self._response


@pytest.mark.asyncio
async def test_route_short_circuits_when_symbol_present() -> None:
    llm = FakeLLM('{"route":"hybrid","confidence":0.9,"reason":"x"}')
    r = QueryRouter(llm=llm)
    decision = await r.route("where is User.login called?")
    assert decision.name == "graph"
    assert decision.symbol == "User.login"
    assert llm.calls == []  # never consulted the LLM


@pytest.mark.asyncio
async def test_route_uses_llm_when_no_symbol() -> None:
    llm = FakeLLM('{"route":"community","confidence":0.85,"reason":"module-level"}')
    r = QueryRouter(llm=llm)
    decision = await r.route("how do auth and billing interact?")
    assert decision.name == "community"
    assert decision.confidence == 0.85
    assert llm.calls and len(llm.calls[0]) == 2


@pytest.mark.asyncio
async def test_route_handles_code_fenced_json() -> None:
    raw = '```json\n{"route":"hybrid","confidence":0.7,"reason":"meh"}\n```'
    llm = FakeLLM(raw)
    r = QueryRouter(llm=llm)
    decision = await r.route("explain authentication")
    assert decision.name == "hybrid"


@pytest.mark.asyncio
async def test_route_unknown_route_falls_back_to_hybrid() -> None:
    llm = FakeLLM('{"route":"???","confidence":0.5,"reason":"???"}')
    r = QueryRouter(llm=llm)
    decision = await r.route("something general")
    assert decision.name == "hybrid"


@pytest.mark.asyncio
async def test_route_llm_failure_returns_hybrid_safe_default() -> None:
    class BoomLLM:
        @property
        def model(self) -> str:
            return "fake"

        async def complete(self, messages: Sequence[ChatMessage]) -> str:
            raise RuntimeError("boom")

    r = QueryRouter(llm=BoomLLM())
    decision = await r.route("explain authentication")
    assert decision.name == "hybrid"
    assert "router LLM unavailable" in decision.reason
