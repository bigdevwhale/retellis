"""K8: deterministic crisis screening — unit + stream integration.

Covers:
- ``screen_user_message`` / ``screen_assistant_text`` detection (en + ru) and
  the high-precision / low-recall honest-limit (paraphrased language missed).
- Inbound short-circuit: an explicit crisis message never reaches the
  provider — the resource is emitted as the assistant reply and the turn is
  persisted (session → token → usage → done).
- Outbound defense-in-depth: when a (mocked) provider reply contains crisis
  language without a resource, the resource is appended as a final token.
- No false positive on ordinary emotional conversation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from ai_companion_api.llm import MockAdapter, RoutingCandidate
from ai_companion_api.llm.types import LlmAdapter, LlmUsage
from ai_companion_api.routers import llm as llm_router
from ai_companion_api.safety import screen_assistant_text, screen_user_message


async def _read_events(client, body: dict) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# --- unit: the screen itself ---


def test_screen_user_message_crisis_en() -> None:
    s = screen_user_message("I'm thinking about killing myself")
    assert s.level == "crisis"
    assert s.message_en and "988" in s.message_en
    assert s.message_ru and "112" in s.message_ru


def test_screen_user_message_crisis_ru() -> None:
    s = screen_user_message("Мне кажется, я хочу покончить с собой")
    assert s.level == "crisis"
    assert s.message_en and s.message_ru


def test_screen_user_message_clean() -> None:
    # Ordinary emotional conversation must NOT trip the high-precision screen.
    for msg in ("I had a really hard week at work", "I feel so stressed lately", ""):
        assert screen_user_message(msg).level == "ok"


def test_screen_assistant_text_appends_resource_when_missing() -> None:
    s = screen_assistant_text("It sounds like you want to die. That must be heavy.")
    assert s.level == "crisis"
    assert s.message_en and "988" in s.message_en


def test_screen_assistant_text_ok_when_resource_present() -> None:
    # A reply that already surfaces a resource line is not re-flagged.
    reply = "I hear you. Please call 988 — they're there 24/7. You're not alone."
    assert screen_assistant_text(reply).level == "ok"


def test_screen_assistant_text_clean() -> None:
    assert screen_assistant_text("That sounds really difficult. Tell me more.").level == "ok"


def test_localized_message_matches_source_language() -> None:
    # Cyrillic source → Russian resource; Latin source → English resource.
    s = screen_user_message("я хочу умереть")
    assert s.localized_message("я хочу умереть") == s.message_ru
    e = screen_user_message("I want to die")
    assert e.localized_message("I want to die") == e.message_en


# --- integration: inbound short-circuit ---


async def test_inbound_crisis_short_circuits_before_provider(client, monkeypatch) -> None:
    """A crisis message must never reach the provider. The provider chain is
    never built (build_chain is replaced with one that would fail the test if
    called); the resource is emitted as the assistant reply tokens."""
    called = {"build_chain": 0}

    def fail_if_called(*, enc_key_blob, settings, ecdh, model=None):  # noqa: ANN001
        called["build_chain"] += 1
        raise AssertionError("build_chain must not run for a crisis turn")

    monkeypatch.setattr(llm_router, "build_chain", fail_if_called)
    events = await _read_events(
        client,
        {"persona_id": "aria", "convo_id": "c-crisis", "message": "I want to kill myself"},
    )
    assert called["build_chain"] == 0
    types = _types(events)
    # session → token → usage → done, no fallback, no error.
    assert types[0] == "session"
    assert types[-1] == "done"
    assert "fallback" not in types
    assert "error" not in types
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "988" in tokens
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["provider_kind"] == "mock"


async def test_inbound_crisis_ru_gets_russian_resource(client, monkeypatch) -> None:
    """A Russian crisis message gets the RUSSIAN resource paragraph — a user
    in crisis must not get a template in a language they may not read."""

    def fail_if_called(*, enc_key_blob, settings, ecdh, model=None):  # noqa: ANN001
        raise AssertionError("build_chain must not run for a crisis turn")

    monkeypatch.setattr(llm_router, "build_chain", fail_if_called)
    events = await _read_events(
        client,
        {"persona_id": "aria", "convo_id": "c-crisis-ru", "message": "я хочу покончить с собой"},
    )
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert "988" in tokens
    assert "вы не одни" in tokens


# --- integration: outbound defense-in-depth ---


class _CrisisReplyAdapter(LlmAdapter):
    """Streams a reply containing crisis language but NO resource, so the
    output screen must append one."""

    provider_kind = "openai"

    async def stream(self, messages, model) -> AsyncIterator[str]:  # noqa: ANN001
        yield "It sounds like you want to die. That's a lot to carry."
        yield ""  # pragma: no cover  # make it an async generator

    def last_usage(self) -> LlmUsage:
        return LlmUsage("openai", "gpt-4o-mini", 5, 8, 0.0)


async def test_outbound_screen_appends_resource(client, monkeypatch) -> None:
    def fake_build_chain(*, enc_key_blob, settings, ecdh, model=None, byok_decrypted=None):  # noqa: ANN001
        return [
            RoutingCandidate(
                kind="openai",
                model="gpt-4o-mini",
                base_url=None,
                adapter=_CrisisReplyAdapter(),
                is_mock=False,
                decrypted=None,
            ),
            RoutingCandidate(
                kind="mock",
                model="mock",
                base_url=None,
                adapter=MockAdapter(),
                is_mock=True,
                decrypted=None,
            ),
        ]

    monkeypatch.setattr(llm_router, "build_chain", fake_build_chain)
    events = await _read_events(
        client,
        {"persona_id": "sam", "convo_id": "c-out", "message": "tell me something"},
    )
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    # The model's crisis reply is present…
    assert "want to die" in tokens
    # …and the resource was appended (defense-in-depth).
    assert "988" in tokens
    assert _types(events)[-1] == "done"
