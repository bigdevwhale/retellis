"""Shared types for LLM adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LlmUsage:
    provider_kind: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class LlmAdapter(Protocol):
    """Streams tokens for one completion. Usage is available after the stream ends."""

    provider_kind: str

    async def stream(self, messages: list[dict[str, str]], model: str) -> AsyncIterator[str]: ...

    def last_usage(self) -> LlmUsage: ...
