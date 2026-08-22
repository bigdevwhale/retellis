"""LLM-judge salience: parsing + fallback behavior.

The judge runs on the model that served the turn; here we stub the adapter's
``complete`` to return canned JSON and assert the parse path, the tolerant
fallbacks (prose-wrapped / fenced / unparseable), the mock-path skip, and the
network-error fallback to None (so the heuristic takes over).
"""

from __future__ import annotations

import pytest

from ai_companion_api.memory.salience_llm import judge_salience


class _FakeAdapter:
    """Minimal adapter stub for the judge. ``complete`` returns a canned reply."""

    provider_kind = "openai"

    def __init__(self, reply: str | None = None, raises: bool = False) -> None:
        self._reply = reply
        self._raises = raises

    async def complete(self, messages: list[dict[str, str]], model: str) -> str:
        if self._raises:
            raise RuntimeError("boom")
        return self._reply or ""


@pytest.mark.asyncio
async def test_judge_parses_clean_json() -> None:
    a = _FakeAdapter(
        '{"salience": 0.83, "short_term_salience": 0.9, "emotional_intensity": 0.7, '
        '"emotion_tags": ["tired","lonely","grief"]}'
    )
    out = await judge_salience(a, "gpt-4o-mini", "я очень устал и мне одиноко")
    assert out is not None
    assert out.salience == pytest.approx(0.83)
    assert out.short_term_salience == pytest.approx(0.9)
    assert out.emotional_intensity == pytest.approx(0.7)
    assert out.emotion_tags == ["tired", "lonely", "grief"]


@pytest.mark.asyncio
async def test_judge_parses_json_wrapped_in_prose() -> None:
    a = _FakeAdapter('Sure — here: {"salience": 0.4, "emotion_tags": ["anxious"]} thanks')
    out = await judge_salience(a, "gpt-4o-mini", "feeling anxious about the deadline")
    assert out is not None
    assert out.salience == pytest.approx(0.4)
    assert out.emotion_tags == ["anxious"]


@pytest.mark.asyncio
async def test_judge_parses_json_in_code_fence() -> None:
    a = _FakeAdapter('```json\n{"salience": 0.1, "emotion_tags": []}\n```')
    out = await judge_salience(a, "gpt-4o-mini", "hi")
    assert out is not None
    assert out.salience == pytest.approx(0.1)
    assert out.emotion_tags == []


@pytest.mark.asyncio
async def test_judge_defaults_missing_dimensions_to_zero() -> None:
    # Old-style reply with only `salience` — the new dimensions default to 0.0
    # instead of failing the parse (models may ignore the extra fields).
    a = _FakeAdapter('{"salience": 0.6, "emotion_tags": ["hope"]}')
    out = await judge_salience(a, "gpt-4o-mini", "msg")
    assert out is not None
    assert out.salience == pytest.approx(0.6)
    assert out.short_term_salience == 0.0
    assert out.emotional_intensity == 0.0


@pytest.mark.asyncio
async def test_judge_clamps_out_of_range_salience() -> None:
    a = _FakeAdapter(
        '{"salience": 5.0, "short_term_salience": -2.0, "emotional_intensity": 9.9, '
        '"emotion_tags": ["joy"]}'
    )
    out = await judge_salience(a, "gpt-4o-mini", "won the lottery")
    assert out is not None
    assert out.salience == 1.0
    assert out.short_term_salience == 0.0
    assert out.emotional_intensity == 1.0


@pytest.mark.asyncio
async def test_judge_caps_tags_at_three_and_lowercases() -> None:
    a = _FakeAdapter('{"salience": 0.5, "emotion_tags": ["Joy","HOPE","Pride","Grief"]}')
    out = await judge_salience(a, "gpt-4o-mini", "msg")
    assert out is not None
    assert out.emotion_tags == ["joy", "hope", "pride"]


@pytest.mark.asyncio
async def test_judge_returns_none_on_unparseable() -> None:
    a = _FakeAdapter("the message is sad")  # no JSON object
    assert await judge_salience(a, "gpt-4o-mini", "msg") is None


@pytest.mark.asyncio
async def test_judge_returns_none_on_empty_reply() -> None:
    a = _FakeAdapter("")
    assert await judge_salience(a, "gpt-4o-mini", "msg") is None


@pytest.mark.asyncio
async def test_judge_returns_none_on_call_error() -> None:
    a = _FakeAdapter(raises=True)
    assert await judge_salience(a, "gpt-4o-mini", "msg") is None


@pytest.mark.asyncio
async def test_judge_skips_empty_text() -> None:
    a = _FakeAdapter('{"salience": 0.9, "emotion_tags": ["x"]}')
    assert await judge_salience(a, "gpt-4o-mini", "   ") is None


@pytest.mark.asyncio
async def test_judge_returns_none_when_adapter_has_no_complete() -> None:
    class Bare:
        provider_kind = "openai"

    assert await judge_salience(Bare(), "gpt-4o-mini", "msg") is None
