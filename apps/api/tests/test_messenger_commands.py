"""Commands + connect-token + poller tests (no network)."""

from __future__ import annotations

import asyncio

import pytest

from ai_companion_api.config import Settings
from ai_companion_api.crypto.envelope import EnvelopeCipher
from ai_companion_api.memory.store import InMemoryStore
from ai_companion_api.messengers.base import (
    AdapterInfo,
    AdapterUpdate,
    ButtonSpec,
    TokenInvalid,
    TransientError,
)
from ai_companion_api.messengers.connect_token import (
    decode_connect_token,
    issue_connect_token,
    verify_connect_token,
)
from ai_companion_api.messengers.polling import MessengerPoller, PollerDeps
from ai_companion_api.messengers.store import InMemoryMessengerStore, MessengerRecord
from ai_companion_api.messengers.telegram.commands import (
    BUILTIN_PERSONAS,
    BotSession,
    handle_update,
)
from ai_companion_api.vault.session_ecdh import generate_session_keypair

# ---- fake adapter (records calls, no network) ----


class FakeAdapter:
    kind = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, object]] = []  # (method, chat_id, payload)
        self.poll_returns: list[list[AdapterUpdate]] = []
        self.poll_raise: Exception | None = None
        self.validate_info = AdapterInfo(bot_id=1, username="bot", can_join_groups=False)

    async def validate_token(self, token: str) -> AdapterInfo:
        if token.startswith("BAD"):
            raise TokenInvalid("nope")
        return self.validate_info

    async def poll_once(self, token, offset, *, timeout=30):  # type: ignore[no-untyped-def]
        if self.poll_raise is not None:
            exc, self.poll_raise = self.poll_raise, None
            raise exc
        return self.poll_returns.pop(0) if self.poll_returns else []

    async def send_text(self, token: str, chat_id: int, text: str) -> None:
        self.sent.append(("text", chat_id, text))

    async def send_typing(self, token: str, chat_id: int) -> None:
        self.sent.append(("typing", chat_id, None))

    async def send_inline(
        self, token: str, chat_id: int, text: str, buttons: list[list[ButtonSpec]]
    ) -> None:
        self.sent.append(("inline", chat_id, (text, buttons)))

    async def answer_callback(self, token: str, callback_id: str, text: str | None) -> None:
        self.sent.append(("callback", 0, (callback_id, text)))


@pytest.fixture
def deps():
    settings = Settings()
    settings = settings.model_copy(update={"auth_state_secret": "test-secret-key-0123456789"})
    store = InMemoryStore()
    ecdh = generate_session_keypair()
    envelope = EnvelopeCipher.from_base64(EnvelopeCipher.generate_key_b64())
    messenger_store = InMemoryMessengerStore()
    adapter = FakeAdapter()
    return PollerDeps(
        settings=settings,
        store=store,
        ecdh=ecdh,
        envelope=envelope,
        messenger_store=messenger_store,
        adapter=adapter,
        public_origin="https://stillside.app",
    )


def _make_messenger(deps, *, status="pending_handshake", persona_id="aria", byok_enc_blob=None):
    cipher = deps.envelope.encrypt_b64(b"123456:fake-bot-token")
    m = MessengerRecord(
        id="m1",
        user_id="u1",
        kind="telegram",
        status=status,
        persona_id=persona_id,
        bot_token_ciphertext=cipher,
        byok_enc_blob=byok_enc_blob,
        bot_token_masked="…OKEN",
    )
    return m


def _session(deps, m, *, token="123456:fake-bot-token", convo_id="c1") -> BotSession:
    return BotSession(
        messenger=m,
        adapter=deps.adapter,
        settings=deps.settings,
        store=deps.store,
        ecdh=deps.ecdh,
        envelope=deps.envelope,
        messenger_store=deps.messenger_store,
        public_origin=deps.public_origin,
        bot_token=token,
        conversation_id=convo_id,
    )


def _upd(chat_id=1, text=None, command=None, args=None, callback_id=None, callback_data=None):
    return AdapterUpdate(
        update_id=10,
        chat_id=chat_id,
        text=text,
        command=command,
        command_args=args,
        callback_id=callback_id,
        callback_data=callback_data,
    )


# ---- connect token ----


def test_connect_token_roundtrip(deps) -> None:
    tok = issue_connect_token(messenger_id="m1", settings=deps.settings)
    assert verify_connect_token(tok, messenger_id="m1", settings=deps.settings) is True
    # Wrong messenger → rejected.
    assert verify_connect_token(tok, messenger_id="other", settings=deps.settings) is False


def test_connect_token_tamper_rejected(deps) -> None:
    tok = issue_connect_token(messenger_id="m1", settings=deps.settings)
    bad = tok[:-1] + ("a" if tok[-1] != "a" else "b")
    assert verify_connect_token(bad, messenger_id="m1", settings=deps.settings) is False


def test_connect_token_decode_returns_mid(deps) -> None:
    tok = issue_connect_token(messenger_id="m99", settings=deps.settings)
    payload = decode_connect_token(tok, settings=deps.settings)
    assert payload is not None
    assert payload["mid"] == "m99"


def test_connect_token_expired_rejected(deps) -> None:
    tok = issue_connect_token(messenger_id="m1", settings=deps.settings, ttl_seconds=-10)
    assert verify_connect_token(tok, messenger_id="m1", settings=deps.settings) is False
    assert decode_connect_token(tok, settings=deps.settings) is None


# ---- commands ----


async def test_start_with_valid_token_sends_connect_link(deps) -> None:
    m = _make_messenger(deps)
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    tok = issue_connect_token(messenger_id=m.id, settings=deps.settings)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="start", args=tok))
    # Should have sent an inline message with the deep link.
    inline = [s for s in deps.adapter.sent if s[0] == "inline"]
    assert inline, "expected an inline connect button"
    text, buttons = inline[0][2]
    assert "messenger=m1" in buttons[0][0].callback_data
    assert "token=" in buttons[0][0].callback_data
    # chat_id persisted on the messenger row.
    assert m.chat_id == 1


async def test_start_with_wrong_token_rejected(deps) -> None:
    m = _make_messenger(deps)
    other_tok = issue_connect_token(messenger_id="other-bot", settings=deps.settings)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="start", args=other_tok))
    text = [s for s in deps.adapter.sent if s[0] == "text"]
    assert "doesn't match" in text[-1][2]


async def test_start_no_token_greets(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="start"))
    text = [s for s in deps.adapter.sent if s[0] == "text"]
    assert "Stillside" in text[-1][2]


async def test_help_command(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="help"))
    assert any(s[0] == "text" and "/persona" in s[2] for s in deps.adapter.sent)


async def test_persona_command_sends_builtin_buttons(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="persona"))
    inline = [s for s in deps.adapter.sent if s[0] == "inline"]
    assert inline
    _, buttons = inline[0][2]
    flat = {b.callback_data for row in buttons for b in row}
    assert flat == {f"persona:{p}" for p in BUILTIN_PERSONAS}


async def test_persona_callback_switches_persona(deps) -> None:
    m = _make_messenger(deps)
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    sess = _session(deps, m)
    await handle_update(sess, _upd(callback_id="cb1", callback_data="persona:sam"))
    assert m.persona_id == "sam"
    # Confirmation + callback ack sent.
    assert any(s[0] == "text" and "sam" in s[2] for s in deps.adapter.sent)
    assert any(s[0] == "callback" for s in deps.adapter.sent)


async def test_clear_rotates_conversation(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m, convo_id="c-old")
    await handle_update(sess, _upd(command="clear"))
    assert sess.conversation_id != "c-old"


async def test_status_reports_persona_and_memory(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="status"))
    text = [s for s in deps.adapter.sent if s[0] == "text"]
    assert "Persona: aria" in text[-1][2]
    assert "BYOK bound: no" in text[-1][2]


async def test_plain_text_runs_turn_and_replies(deps) -> None:
    m = _make_messenger(deps)
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    sess = _session(deps, m)
    await handle_update(sess, _upd(text="I'm feeling a bit tired today"))
    # typing + a text reply (mock adapter echoes the snippet).
    assert any(s[0] == "typing" for s in deps.adapter.sent)
    replies = [s for s in deps.adapter.sent if s[0] == "text"]
    assert replies
    assert "tired today" in replies[-1][2]
    # chat_id learned from the first message.
    assert m.chat_id == 1
    # The turn persisted a usage row in the shared store.
    usage = await deps.store.list_usage(user_id="u1")
    assert len(usage) == 1


async def test_unknown_command(deps) -> None:
    m = _make_messenger(deps)
    sess = _session(deps, m)
    await handle_update(sess, _upd(command="frobnicate"))
    assert any(s[0] == "text" and "Unknown" in s[2] for s in deps.adapter.sent)


async def test_handle_update_swallows_exceptions(deps) -> None:
    """A command handler raising must NOT propagate — the poller must keep
    running (same best-effort contract as the web path)."""
    m = _make_messenger(deps)
    sess = _session(deps, m)

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    # Patch the adapter to raise on send_text; /help uses send_text.
    deps.adapter.send_text = boom  # type: ignore[assignment]
    # Must not raise.
    await handle_update(sess, _upd(command="help"))


# ---- poller ----


async def test_poller_dispatches_and_persists_offset(deps) -> None:
    m = _make_messenger(deps, status="active")
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    deps.adapter.poll_returns = [
        [_upd(chat_id=42, text="hello there", command=None)],
        [],  # second cycle: no updates
    ]
    poller = MessengerPoller(deps, m)
    await poller.start()
    # Let it run two cycles (one with an update, one idle).
    await asyncio.sleep(0.2)
    await poller.stop()
    # Offset advanced past update_id 10.
    assert m.next_offset == 11
    # The text update ran a turn → a reply was sent.
    assert any(s[0] == "text" and "hello there" in s[2] for s in deps.adapter.sent)


async def test_poller_transient_error_backs_off_then_recovers(deps) -> None:
    m = _make_messenger(deps, status="active")
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    # First poll raises transient, second succeeds with an update.
    deps.adapter.poll_raise = TransientError("5xx")
    deps.adapter.poll_returns = [[_upd(chat_id=7, text="after recover")]]
    poller = MessengerPoller(deps, m)
    # Speed up backoff by patching _BACKOFF_FLOOR via the instance.
    poller._backoff = 0.01
    await poller.start()
    await asyncio.sleep(0.3)
    await poller.stop()
    assert m.status == "active"  # recovered, not marked error
    assert any(s[0] == "text" for s in deps.adapter.sent)


async def test_poller_token_invalid_marks_error_and_stops(deps) -> None:
    m = _make_messenger(deps, status="active")
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    deps.adapter.poll_raise = TokenInvalid("revoked")
    poller = MessengerPoller(deps, m)
    await poller.start()
    await asyncio.sleep(0.2)
    assert m.status == "error"
    assert "rejected" in (m.last_error or "")
    assert not poller.is_running()


async def test_poller_undecryptable_token_marks_error(deps) -> None:
    m = _make_messenger(deps, status="active")
    m.bot_token_ciphertext = "not-valid-base64-envelope"
    deps.messenger_store._by_id[m.id] = m  # type: ignore[attr-defined]
    poller = MessengerPoller(deps, m)
    await poller.start()
    await asyncio.sleep(0.2)
    assert m.status == "error"
    assert "undecryptable" in (m.last_error or "")