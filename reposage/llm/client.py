"""LiteLLM-backed chat client with retry + tracing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ChatMessage:
    role: str
    content: str


class LLMClient:
    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 1024) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        # Phase 2: route via litellm.acompletion with structured logging + OTel span.
        raise NotImplementedError
