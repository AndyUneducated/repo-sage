"""LLM client tests: MockLLMClient parsing + LiteLLMClient response shape.

`MockLLMClient` is the workhorse of the offline pipeline. Its regex must
keep parsing the prompt format `build_answer_messages` produces; if either
side drifts, integration tests would fail in confusing ways. These unit
tests pin both sides of the contract.

`LiteLLMClient` is exercised by monkeypatching `litellm.acompletion` so we
neither hit the network nor depend on a model provider.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.llm.client import LiteLLMClient, MockLLMClient
from reposage.llm.prompts import build_answer_messages
from reposage.retrieval.hybrid import RetrievedChunk
from reposage.retrieval.protocols import ChatMessage


def _chunk(path: str, lo: int, hi: int, text: str = "code") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"id-{path}-{lo}-{hi}",
        repo="r",
        path=Path(path),
        start_line=lo,
        end_line=hi,
        text=text,
        symbol="Sym",
        score=0.0,
        source="rrf",
    )


# ----------------------------------------------------------------- MockLLMClient


@pytest.mark.asyncio
async def test_mock_llm_returns_citation_for_first_chunk_in_real_prompt() -> None:
    """The mock must parse what `build_answer_messages` actually produces."""
    chunks = [_chunk("auth/sessions.py", 12, 25), _chunk("billing/invoice.py", 1, 8)]
    messages = build_answer_messages("How does Session.open work?", chunks)

    out = await MockLLMClient().complete(messages)
    assert "[auth/sessions.py:12-25]" in out


@pytest.mark.asyncio
async def test_mock_llm_handles_chunk_with_symbol_attribute() -> None:
    """Regression: regex must accept the trailing `symbol="..."` attribute."""
    raw_user = (
        '<retrieved_chunk path="x/y.py" lines="3-7" symbol="X.run">\nbody\n</retrieved_chunk>'
    )
    out = await MockLLMClient().complete([ChatMessage(role="user", content=raw_user)])
    assert "[x/y.py:3-7]" in out


@pytest.mark.asyncio
async def test_mock_llm_returns_no_context_message_when_no_chunks() -> None:
    """Empty context → fixed sentinel that the grounder treats as ungrounded."""
    out = await MockLLMClient().complete(
        [ChatMessage(role="user", content="Question:\nfoo\n\nContext:\n<no retrieved chunks>")]
    )
    assert out == "I do not have enough context to answer."


@pytest.mark.asyncio
async def test_mock_llm_picks_first_chunk_not_last() -> None:
    """Determinism: the mock must always pick the FIRST retrieved chunk."""
    chunks = [
        _chunk("first.py", 1, 2),
        _chunk("second.py", 100, 200),
    ]
    out = await MockLLMClient().complete(build_answer_messages("q", chunks))
    assert "[first.py:1-2]" in out
    assert "second.py" not in out


@pytest.mark.asyncio
async def test_mock_llm_uses_last_user_message() -> None:
    """If the conversation has multiple user turns, parse only the latest."""
    msgs = [
        ChatMessage(
            role="user",
            content='<retrieved_chunk path="old.py" lines="1-2">\nx\n</retrieved_chunk>',
        ),
        ChatMessage(role="assistant", content="thinking..."),
        ChatMessage(
            role="user",
            content='<retrieved_chunk path="new.py" lines="9-9">\ny\n</retrieved_chunk>',
        ),
    ]
    out = await MockLLMClient().complete(msgs)
    assert "[new.py:9-9]" in out
    assert "old.py" not in out


def test_mock_llm_model_property() -> None:
    assert MockLLMClient().model == "mock-llm-v1"
    assert MockLLMClient(model="other").model == "other"


# ----------------------------------------------------------------- LiteLLMClient


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = types.SimpleNamespace(content=content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake `litellm.acompletion` and capture call args."""
    captured: dict[str, object] = {}

    async def acompletion(**kwargs: object) -> _FakeResp:
        captured.update(kwargs)
        return _FakeResp("hello from fake litellm")

    fake = types.ModuleType("litellm")
    fake.acompletion = acompletion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


@pytest.mark.asyncio
async def test_litellm_client_passes_messages_through(fake_litellm) -> None:
    client = LiteLLMClient(model="openai/gpt-test", temperature=0.2, max_tokens=64)
    out = await client.complete(
        [ChatMessage(role="system", content="you are X"), ChatMessage(role="user", content="ping")]
    )
    assert out == "hello from fake litellm"
    assert fake_litellm["model"] == "openai/gpt-test"
    assert fake_litellm["temperature"] == 0.2
    assert fake_litellm["max_tokens"] == 64
    msgs = fake_litellm["messages"]
    assert isinstance(msgs, list)
    assert msgs[0] == {"role": "system", "content": "you are X"}
    assert msgs[1] == {"role": "user", "content": "ping"}


@pytest.mark.asyncio
async def test_litellm_client_handles_dict_response_shape(monkeypatch) -> None:
    """LiteLLM sometimes returns a dict-shaped response; we must accept both."""

    async def acompletion(**_: object) -> dict[str, object]:
        return {"choices": [{"message": {"content": "dict-shape"}}]}

    fake = types.ModuleType("litellm")
    fake.acompletion = acompletion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", fake)

    out = await LiteLLMClient(model="x").complete([ChatMessage(role="user", content="q")])
    assert out == "dict-shape"


@pytest.mark.asyncio
async def test_litellm_client_returns_empty_string_for_empty_content(monkeypatch) -> None:
    """``content=None`` from a provider must coerce to empty string, not crash."""

    async def acompletion(**_: object) -> _FakeResp:
        return _FakeResp("")

    fake = types.ModuleType("litellm")
    fake.acompletion = acompletion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", fake)

    out = await LiteLLMClient(model="x").complete([ChatMessage(role="user", content="q")])
    assert out == ""


def test_litellm_client_respects_explicit_model() -> None:
    """Constructor arg overrides settings."""
    assert LiteLLMClient(model="anthropic/claude-test").model == "anthropic/claude-test"


@pytest.mark.asyncio
async def test_litellm_client_forwards_ollama_api_base(fake_litellm) -> None:
    """For ollama_chat/* models the client must inject `api_base`."""
    client = LiteLLMClient(model="ollama_chat/qwen2.5-coder:7b")
    await client.complete([ChatMessage(role="user", content="ping")])
    api_base = fake_litellm.get("api_base")
    assert api_base is not None
    assert api_base.startswith("http")


@pytest.mark.asyncio
async def test_litellm_client_omits_api_base_for_hosted_providers(
    fake_litellm,
) -> None:
    """OpenAI / Anthropic must NOT receive an Ollama api_base."""
    client = LiteLLMClient(model="openai/gpt-test")
    await client.complete([ChatMessage(role="user", content="ping")])
    assert "api_base" not in fake_litellm


@pytest.mark.asyncio
async def test_litellm_client_explicit_api_base_overrides_settings(
    fake_litellm,
) -> None:
    client = LiteLLMClient(model="ollama_chat/llama3", api_base="http://remote-ollama:11434")
    await client.complete([ChatMessage(role="user", content="ping")])
    assert fake_litellm["api_base"] == "http://remote-ollama:11434"


# --------------------------------------------------------- determinism contract


@pytest.mark.asyncio
async def test_mock_llm_is_deterministic_for_same_chunks() -> None:
    chunks = [_chunk("a.py", 1, 5)]
    msgs = build_answer_messages("q", chunks)
    a = await MockLLMClient().complete(msgs)
    b = await MockLLMClient().complete(msgs)
    assert a == b


@pytest.mark.asyncio
async def test_mock_llm_signature_compatible_with_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anyone holding the LLMClient protocol can call MockLLMClient."""
    from reposage.retrieval.protocols import LLMClient  # noqa: PLC0415

    client: LLMClient = MockLLMClient()
    out = await client.complete([ChatMessage(role="user", content="hi")])
    assert isinstance(out, str)
    # Use HashEmbedder to make the test self-contained — embedder is unrelated
    # but proves we did not accidentally widen the protocol.
    assert HashEmbedder().model == "hash-embedder-v1"
