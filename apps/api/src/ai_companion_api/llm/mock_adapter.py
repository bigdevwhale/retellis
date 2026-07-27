"""Deterministic mock LLM adapter.

Used when no BYOK key and no server-fallback env key are available, so
``docker compose up`` works zero-config. Also used by the eval harness so the
empathy gate measures *context construction*, not model quality (PLAN §6).

The reply is honest about being a stand-in (disclose, don't perform), echoes the
user's last message, and asks one reflective question — fully deterministic,
no randomness, no clock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .types import LlmUsage


def _words(text: str) -> list[str]:
    return text.split()


class MockAdapter:
    provider_kind = "mock"

    def __init__(self) -> None:
        self._usage: LlmUsage | None = None

    async def stream(self, messages: list[dict[str, str]], model: str) -> AsyncIterator[str]:
        last_user = ""
        prompt_words = 0
        for m in messages:
            content = m.get("content", "")
            prompt_words += len(_words(content))
            if m.get("role") == "user":
                last_user = content

        snippet = last_user.strip().replace("\n", " ")
        if len(snippet) > 140:
            snippet = snippet[:137] + "…"

        reply = (
            "(offline stand-in — no provider key connected) "
            f"I hear that: “{snippet}”. "
            "What feels like the next small step from here?"
        )

        completion_words = _words(reply)
        self._usage = LlmUsage(
            provider_kind="mock",
            model=model or "mock",
            prompt_tokens=prompt_words,
            completion_tokens=len(completion_words),
            cost_usd=0.0,
        )

        for word in completion_words:
            yield word + " "

    def last_usage(self) -> LlmUsage:
        if self._usage is None:
            return LlmUsage("mock", "mock", 0, 0, 0.0)
        return self._usage
