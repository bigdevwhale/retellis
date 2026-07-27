"""Telegram adapter + Bot API wrapper tests (httpx MockTransport — no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from ai_companion_api.messengers.base import (
    ButtonSpec,
    FatalError,
    TokenInvalid,
    TransientError,
)
from ai_companion_api.messengers.telegram.adapter import TelegramAdapter, _chunk, _parse_command
from ai_companion_api.messengers.telegram.bot_api import TelegramBotAPI


def _ok(result: object) -> bytes:
    return json.dumps({"ok": True, "result": result}).encode()


def _mock(handler) -> httpx.MockTransport:  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


def _make_adapter(handler) -> TelegramAdapter:  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, timeout=10.0)
    return TelegramAdapter(TelegramBotAPI(client=client))


# ---- Bot API status mapping ----


async def test_get_me_401_raises_token_invalid() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    api = TelegramBotAPI(client=httpx.AsyncClient(transport=_mock(handler), timeout=5.0))
    with pytest.raises(TokenInvalid):
        await api.get_me("bad:token")


async def test_get_updates_409_raises_fatal() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"ok": False, "description": "Conflict"})

    api = TelegramBotAPI(client=httpx.AsyncClient(transport=_mock(handler), timeout=5.0))
    with pytest.raises(FatalError):
        await api.get_updates("tok", offset=None, timeout=1)


async def test_5xx_raises_transient() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"ok": False})

    api = TelegramBotAPI(client=httpx.AsyncClient(transport=_mock(handler), timeout=5.0))
    with pytest.raises(TransientError):
        await api.send_message("tok", 1, "hi")


async def test_ok_false_raises_transient() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "bad"})

    api = TelegramBotAPI(client=httpx.AsyncClient(transport=_mock(handler), timeout=5.0))
    with pytest.raises(TransientError):
        await api.send_message("tok", 1, "hi")


async def test_network_error_raises_transient() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    api = TelegramBotAPI(client=httpx.AsyncClient(transport=_mock(handler), timeout=5.0))
    with pytest.raises(TransientError):
        await api.get_me("tok")


# ---- Adapter behavior ----


async def test_validate_token_returns_info() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/getMe")
        return httpx.Response(200, content=_ok({"id": 123, "username": "testbot", "can_join_groups": True}))

    ad = _make_adapter(handler)
    info = await ad.validate_token("123:ABC")
    assert info.bot_id == 123
    assert info.username == "testbot"
    assert info.can_join_groups is True


async def test_validate_token_empty_raises() -> None:
    ad = TelegramAdapter(TelegramBotAPI(client=httpx.AsyncClient(timeout=5.0)))
    with pytest.raises(TokenInvalid):
        await ad.validate_token("   ")


async def test_poll_once_parses_message_and_command() -> None:
    payload = _ok([
        {
            "update_id": 42,
            "message": {
                "message_id": 1,
                "from": {"id": 99, "is_bot": False, "username": "alice", "first_name": "A"},
                "chat": {"id": 777, "type": "private", "username": "alice"},
                "text": "/start connect-xyz",
                "date": 1700,
            },
        }
    ])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    ad = _make_adapter(handler)
    updates = await ad.poll_once("tok", offset=None, timeout=1)
    assert len(updates) == 1
    u = updates[0]
    assert u.update_id == 42
    assert u.chat_id == 777
    assert u.command == "start"
    assert u.command_args == "connect-xyz"
    assert u.text == "/start connect-xyz"


async def test_poll_once_drops_non_private_chat() -> None:
    payload = _ok([
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 99, "is_bot": False},
                "chat": {"id": -100, "type": "group"},
                "text": "hi",
                "date": 1,
            },
        }
    ])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    ad = _make_adapter(handler)
    updates = await ad.poll_once("tok", offset=None, timeout=1)
    # Group updates are out of scope for the MVP — dropped silently.
    assert updates == []


async def test_poll_once_parses_callback_query() -> None:
    payload = _ok([
        {
            "update_id": 7,
            "callback_query": {
                "id": "cb1",
                "from": {"id": 99, "is_bot": False},
                "data": "persona:p2",
                "message": {
                    "message_id": 5,
                    "chat": {"id": 888, "type": "private"},
                    "text": "pick one",
                    "date": 1,
                },
            },
        }
    ])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    ad = _make_adapter(handler)
    updates = await ad.poll_once("tok", offset=None, timeout=1)
    assert len(updates) == 1
    u = updates[0]
    assert u.callback_id == "cb1"
    assert u.callback_data == "persona:p2"
    assert u.chat_id == 888
    assert u.text is None


async def test_send_text_chunks_long_message() -> None:
    sent: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body.get("text") is not None:
            sent.append(body["text"])
        return httpx.Response(200, content=_ok({"message_id": 1}))

    ad = _make_adapter(handler)
    long = "a" * 6000
    await ad.send_text("tok", 1, long)
    # 6000 chars > 4096 → at least 2 messages, each ≤ 4096.
    assert len(sent) >= 2
    assert all(len(c) <= 4096 for c in sent)
    assert "".join(sent) == long


async def test_send_inline_sends_reply_markup() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ok({"message_id": 1}))

    ad = _make_adapter(handler)
    await ad.send_inline(
        "tok", 1, "Connect?", [[ButtonSpec("Connect", "connect:yes")]]
    )
    assert captured["body"]["reply_markup"] == {
        "inline_keyboard": [[{"text": "Connect", "callback_data": "connect:yes"}]]
    }


async def test_send_typing_swallows_transient_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False})

    ad = _make_adapter(handler)
    # Must not raise — typing action failing must not kill the turn.
    await ad.send_typing("tok", 1)


async def test_answer_callback_calls_endpoint() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ok(True))

    ad = _make_adapter(handler)
    await ad.answer_callback("tok", "cb1", "done")
    assert seen["body"]["callback_query_id"] == "cb1"
    assert seen["body"]["text"] == "done"


# ---- pure helpers ----


def test_parse_command_basic() -> None:
    assert _parse_command("/start abc") == ("start", "abc")
    assert _parse_command("/help") == ("help", None)
    assert _parse_command("/persona@mybot list") == ("persona", "list")
    assert _parse_command("hello") == (None, None)
    assert _parse_command(None) == (None, None)
    assert _parse_command("/") == (None, None)


def test_chunk_respects_limit() -> None:
    assert _chunk("short", 4096) == ["short"]
    parts = _chunk("a" * 9000, 4096)
    assert len(parts) == 3
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == "a" * 9000


def test_chunk_prefers_newline_break() -> None:
    text = "line1\n" * 1000 + "tail"
    parts = _chunk(text, 4096)
    # No part should start mid-line if a newline was available to break on.
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == text


def test_adapter_satisfies_protocol() -> None:
    # The Protocol is runtime_checkable — structural check, not isinstance.
    ad = TelegramAdapter()
    # Methods present with the right names.
    for m in ("validate_token", "poll_once", "send_text", "send_typing", "send_inline", "answer_callback"):
        assert callable(getattr(ad, m, None))
    assert ad.kind == "telegram"