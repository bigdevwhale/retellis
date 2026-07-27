"""Telegram inline-command dispatcher + plain-text turn handler.

``handle_update`` is called by the poller for every normalized update. It
dispatches slash-commands (``/start``, ``/help``, ``/persona``, ``/clear``,
``/status``) and callback queries (inline-button presses), and runs a full
``turn`` for plain text — the same empathy path as the web chat (shared event
chain + persona block + salience-gated extraction).

Honest-limits copy: the bot never fabricates affect (``/start`` doesn't say
"I missed you"; it states what it is and how to connect). ``/status`` reports
the bound persona + memory size as observed, never generated mood.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...observability import redact
from ..base import ButtonSpec
from ..connect_token import verify_connect_token

if TYPE_CHECKING:
    from ...config import Settings
    from ...crypto.envelope import EnvelopeCipher
    from ...memory.store import MemoryStore
    from ...vault.session_ecdh import SessionECDH
    from ..base import MessengerAdapter
    from ..store import MessengerRecord, MessengerStore

logger = logging.getLogger(__name__)

# Builtin personas offered by /persona (mirrors memory/persona_block._BUILTIN).
BUILTIN_PERSONAS = ["aria", "sam", "nico", "mira", "lou"]

HELP_TEXT = (
    "I'm your Stillside companion on Telegram — same memory, same persona as "
    "the web app.\n\n"
    "/help — this message\n"
    "/persona — switch persona\n"
    "/clear — start a fresh conversation thread\n"
    "/status — current persona + memory size\n"
    "\nJust send a message to talk."
)


@dataclass
class BotSession:
    """Per-bot runtime state owned by the poller.

    ``conversation_id`` is the event-chain thread id for this bot's current
    conversation; ``/clear`` rotates it. On a server restart a fresh id is
    generated (memory is still recalled across threads via ``recall_chains``).
    ``bot_token`` is the envelope-decrypted plaintext, alive for one poll
    cycle; the poller zeroizes the source bytearray afterwards.
    """

    messenger: MessengerRecord
    adapter: MessengerAdapter
    settings: Settings
    store: MemoryStore
    ecdh: SessionECDH
    envelope: EnvelopeCipher | None
    messenger_store: MessengerStore
    public_origin: str
    bot_token: str
    conversation_id: str

    @classmethod
    def fresh_conversation_id(cls) -> str:
        return uuid.uuid4().hex


async def handle_update(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    """Dispatch one update. Never raises — a command failure is logged and the
    poller keeps running (same best-effort contract as the web path)."""
    try:
        if update.callback_id is not None:
            await _handle_callback(session, update)
            return
        if update.command is not None:
            await _handle_command(session, update)
            return
        if update.text is not None:
            await _handle_text(session, update)
    except Exception as exc:  # noqa: BLE001 — never kill the poller over one update
        logger.warning(
            "telegram handle_update failed (messenger=%s): %s: %s",
            session.messenger.id,
            type(exc).__name__,
            exc,
        )


async def _handle_command(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    cmd = update.command
    if cmd == "start":
        await _cmd_start(session, update)
    elif cmd == "help":
        await session.adapter.send_text(session.bot_token, update.chat_id, HELP_TEXT)
    elif cmd == "persona":
        await _cmd_persona(session, update)
    elif cmd == "clear":
        session.conversation_id = BotSession.fresh_conversation_id()
        await session.adapter.send_text(
            session.bot_token,
            update.chat_id,
            "Starting a fresh conversation thread. Your memory is still here — "
            "I'll recall it as needed.",
        )
    elif cmd == "status":
        await _cmd_status(session, update)
    else:
        await session.adapter.send_text(
            session.bot_token, update.chat_id, "Unknown command. /help for the list."
        )


async def _cmd_start(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    token = update.command_args
    m = session.messenger
    # If already active, greet without re-running the handshake.
    if m.status == "active":
        await session.adapter.send_text(
            session.bot_token,
            update.chat_id,
            f"You're connected. Persona: {m.persona_id}. Just send a message, or /help.",
        )
        return
    if not token:
        await session.adapter.send_text(
            session.bot_token,
            update.chat_id,
            "Hi! I'm a Stillside companion bot. To connect me to your account, "
            "open Stillside → Settings → Integrations → Telegram and follow the steps.",
        )
        return
    # Verify the token binds to THIS messenger.
    if not verify_connect_token(token, messenger_id=m.id, settings=session.settings):
        await session.adapter.send_text(
            session.bot_token,
            update.chat_id,
            "That connect link doesn't match this bot, or it expired. "
            "Open Stillside → Settings → Integrations to get a fresh one.",
        )
        return
    # Stash the chat_id so the web UI can show it + so we know where to send.
    await session.messenger_store.update(m.id, chat_id=update.chat_id)
    # Deep link into the web handshake page.
    url = f"{session.public_origin.rstrip('/')}/connect/telegram?messenger={m.id}&token={token}"
    await session.adapter.send_inline(
        session.bot_token,
        update.chat_id,
        "Connect this bot to your Stillside account?\n\n"
        "Your BYOK keys stay zero-knowledge: the web page unlocks your vault and "
        "seals the keys to the server for the bot to use during a reply. The "
        "server can't see your passphrase.",
        [[ButtonSpec("Connect to Stillside", url)]],
    )


async def _cmd_persona(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    rows = [[ButtonSpec(pid, f"persona:{pid}")] for pid in BUILTIN_PERSONAS]
    await session.adapter.send_inline(
        session.bot_token,
        update.chat_id,
        f"Current persona: {session.messenger.persona_id}. Pick one to switch:",
        rows,
    )


async def _cmd_status(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    m = session.messenger
    try:
        memories = await session.store.list_memories(
            user_id=m.user_id, persona_id=m.persona_id, include_donors=False
        )
        active = sum(1 for x in memories if x.status.value == "active" if hasattr(x.status, "value"))
    except Exception:  # noqa: BLE001
        active = -1
    mem_line = f"{active} memories" if active >= 0 else "memory unavailable"
    await session.adapter.send_text(
        session.bot_token,
        update.chat_id,
        f"Persona: {m.persona_id}\nStatus: {m.status}\n{mem_line}\n"
        f"BYOK bound: {'yes' if m.byok_enc_blob else 'no (server fallback)'}",
    )


async def _handle_callback(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    data = update.callback_data or ""
    # Acknowledge the callback so Telegram stops the spinner.
    await session.adapter.answer_callback(session.bot_token, update.callback_id, None)
    if data.startswith("persona:"):
        new_persona = data.split(":", 1)[1]
        if new_persona not in BUILTIN_PERSONAS:
            await session.adapter.send_text(
                session.bot_token, update.chat_id, "Unknown persona."
            )
            return
        await session.messenger_store.update(session.messenger.id, persona_id=new_persona)
        # Reflect on the session record so the next turn uses it.
        session.messenger.persona_id = new_persona
        await session.adapter.send_text(
            session.bot_token, update.chat_id, f"Persona switched to {new_persona}."
        )


async def _handle_text(session: BotSession, update) -> None:  # type: ignore[no-untyped-def]
    from ...turn import TurnInput, run_turn

    m = session.messenger
    await session.adapter.send_typing(session.bot_token, update.chat_id)
    inp = TurnInput(
        user_id=m.user_id,
        persona_id=m.persona_id,
        conversation_id=session.conversation_id,
        user_message=update.text or "",
        byok_enc_blob=m.byok_enc_blob,
        source="telegram",
    )
    out = await run_turn(
        inp,
        settings=session.settings,
        store=session.store,
        ecdh=session.ecdh,
        envelope=session.envelope,
    )
    # Record the chat_id (learned on first message) so the web UI can show it.
    if m.chat_id is None:
        try:
            await session.messenger_store.update(m.id, chat_id=update.chat_id)
            m.chat_id = update.chat_id
        except Exception:  # noqa: BLE001
            pass
    await session.adapter.send_text(session.bot_token, update.chat_id, out.assistant_text)
    logger.info(
        "telegram turn done (messenger=%s persona=%s provider=%s fallback=%s)",
        m.id,
        redact(m.persona_id),
        out.provider_kind,
        out.fallback_used,
    )


__all__ = ["BUILTIN_PERSONAS", "BotSession", "HELP_TEXT", "handle_update"]