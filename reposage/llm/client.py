"""LiteLLM-backed chat client + a deterministic mock for CI without secrets.

Why LiteLLM (DD-007): the answering model, the router model, and the
community-summariser model are all different and can be on different
providers. A single string switches each.

Why a mock client: the eval-gate workflow runs without secrets on PRs from
forks. The mock client returns a deterministic answer that cites the first
context chunk verbatim — enough to drive Phase 2 plumbing tests and to
demonstrate the citation-grounding fallback works.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from reposage.config import get_settings
from reposage.retrieval.protocols import ChatMessage


class LiteLLMClient:
    """Async wrapper around `litellm.acompletion`.

    For Ollama-style models (`ollama/...`, `ollama_chat/...`) we forward
    `settings.ollama_api_base` as `api_base` so a non-default Ollama port
    or a remote box can be configured purely via env. LiteLLM's own
    OLLAMA_API_BASE env var is also honored as a fallback.
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        api_base: str | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.llm_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Only forward api_base when the model actually targets Ollama;
        # other providers pick up creds from env vars set by litellm.
        if api_base is not None:
            self._api_base: str | None = api_base
        elif self._model.startswith(("ollama/", "ollama_chat/")):
            self._api_base = settings.ollama_api_base
        else:
            self._api_base = None

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        from litellm import acompletion  # noqa: PLC0415

        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        resp = await acompletion(**kwargs)
        # LiteLLM normalises responses to OpenAI shape; first choice is canonical.
        # Both attribute-access (litellm objects) and dict-access (raw OpenAI
        # SDK shape) are accepted because providers differ in what they return.
        choices = getattr(resp, "choices", None)
        if choices is None:
            choices = resp["choices"]
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None:
            message = first["message"]
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content", "")
        return str(content or "")


class MockLLMClient:
    """Deterministic LLM stand-in.

    Picks the first ``<retrieved_chunk>`` block from the user message and
    crafts an answer that quotes its path/line span so citation-grounding
    succeeds. If no retrieved chunk is present, returns a fixed "no context"
    answer so the grounder triggers its fallback path.
    """

    _CHUNK_RE = re.compile(
        r"<retrieved_chunk\s+path=\"(?P<path>[^\"]+)\"\s+lines=\"(?P<lo>\d+)-(?P<hi>\d+)\""
    )

    def __init__(self, model: str = "mock-llm-v1") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        m = self._CHUNK_RE.search(last_user)
        if m is None:
            return "I do not have enough context to answer."
        path = m.group("path")
        lo = m.group("lo")
        hi = m.group("hi")
        return f"Based on the retrieved code, the relevant logic lives in [{path}:{lo}-{hi}]."


# Backwards-compatible alias for the Phase 1 stub.
LLMClient = LiteLLMClient
