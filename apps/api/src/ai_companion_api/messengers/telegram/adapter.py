"""TelegramAdapter — implements ``MessengerAdapter`` over ``TelegramBotAPI``.

Translates vendor JSON into the adapter-agnostic ``AdapterUpdate`` shape and
maps Bot API errors to the base exception taxonomy. Command parsing
(``/start <token>``, ``/help``, ...) happens here so the poller/commands layer
stays vendor-neutral.
"""

from __future__ import annotations

import httpx

from ..base import (
    AdapterInfo,
    AdapterUpdate,
    ButtonSpec,
    TokenInvalid,
    TransientError,
)
from .bot_api import TelegramBotAPI
from .types import parse_update

# Telegram messages cap at 4096 chars; send in chunks to stay under the limit.
TG_MAX_LEN = 4096


class TelegramAdapter:
    """One instance serves all Telegram bots (token passed per call)."""

    kind = "telegram"

    def __init__(self, api: TelegramBotAPI | None = None) -> None:
        self._api = api or TelegramBotAPI()

    @staticmethod
    def _normalize_token(token: str) -> str:
        t = token.strip()
        if not t:
            raise TokenInvalid("empty bot token")
        return t

    async def validate_token(self, token: str) -> AdapterInfo:
        t = self._normalize_token(token)
        me = await self._api.get_me(t)
        if not me:
            raise TokenInvalid("getMe returned empty result")
        return AdapterInfo(
            bot_id=int(me.get("id", 0)),
            username=str(me.get("username", "")),
            can_join_groups=bool(me.get("can_join_groups", False)),
        )

    async def poll_once(
        self, token: str, offset: int | None, *, timeout: int = 30
    ) -> list[AdapterUpdate]:
        t = self._normalize_token(token)
        raw_updates = await self._api.get_updates(t, offset=offset, timeout=timeout)
        out: list[AdapterUpdate] = []
        for raw in raw_updates:
            if not isinstance(raw, dict):
                continue
            upd = parse_update(raw)
            # Only private chats are in scope for the MVP (1:1 DM = personal
            # account + persona). Group/other updates are dropped here.
            if upd.message is not None:
                msg = upd.message
                if msg.chat.type != "private":
                    continue
                command, args = _parse_command(msg.text)
                out.append(
                    AdapterUpdate(
                        update_id=upd.update_id,
                        chat_id=msg.chat.id,
                        text=msg.text,
                        command=command,
                        command_args=args,
                        raw=raw,
                    )
                )
            elif upd.callback_query is not None:
                cb = upd.callback_query
                # Resolve the chat_id from the callback's message (the button
                # was attached to a message in the same chat).
                chat_id = cb.message.chat.id if cb.message is not None else 0
                out.append(
                    AdapterUpdate(
                        update_id=upd.update_id,
                        chat_id=chat_id,
                        callback_id=cb.id,
                        callback_data=cb.data,
                        raw=raw,
                    )
                )
        return out

    async def send_text(self, token: str, chat_id: int, text: str) -> None:
        t = self._normalize_token(token)
        # Chunk to respect the 4096-char ceiling; a long companion reply would
        # otherwise 400. Split on paragraph boundaries when possible.
        for chunk in _chunk(text, TG_MAX_LEN):
            await self._api.send_message(t, chat_id, chunk)

    async def send_typing(self, token: str, chat_id: int) -> None:
        t = self._normalize_token(token)
        # sendChatAction returns 400 on an unknown chat; treat as transient so
        # the poller doesn't die if the user closed the chat mid-turn.
        try:
            await self._api.send_chat_action(t, chat_id, "typing")
        except TransientError:
            pass

    async def send_inline(
        self, token: str, chat_id: int, text: str, buttons: list[list[ButtonSpec]]
    ) -> None:
        t = self._normalize_token(token)
        markup = {
            "inline_keyboard": [[{"text": b.text, "callback_data": b.callback_data} for b in row] for row in buttons]
        }
        await self._api.send_message_with_inline(t, chat_id, text, markup)

    async def answer_callback(self, token: str, callback_id: str, text: str | None) -> None:
        t = self._normalize_token(token)
        await self._api.answer_callback_query(t, callback_id, text)


def _parse_command(text: str | None) -> tuple[str | None, str | None]:
    """Split ``/cmd args`` → (``cmd``, ``args``). Returns (None, None) for
    non-command text. The leading ``/`` is stripped; args keep their case."""
    if not text or not text.startswith("/"):
        return None, None
    # Telegram may append the bot username (``/cmd@bot arg``); split on
    # whitespace then on ``@``.
    head, _, rest = text.partition(" ")
    cmd = head[1:].split("@", 1)[0].lower()
    args = rest.strip() or None
    return (cmd or None), args


def _chunk(text: str, size: int) -> list[str]:
    """Split ``text`` into chunks ≤ ``size`` chars, preserving content exactly
    (``"".join(parts) == text``). Prefers to break on a newline, then on a
    space, within the last 20% of the window; falls back to a hard cut."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > size:
        # Prefer a newline in the last 20% of the window (include the newline
        # in the left half so round-trip join is exact).
        cut = remaining.rfind("\n", 0, size)
        if cut >= int(size * 0.8):
            cut += 1
        else:
            cut = remaining.rfind(" ", 0, size)
            if cut >= int(size * 0.8):
                cut += 1
            else:
                cut = size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


__all__ = ["TelegramAdapter"]


# Re-export for the registry + tests so they don't import the Bot API directly.
def build_client(transport: httpx.BaseTransport | None = None) -> httpx.AsyncClient:
    """Construct an ``httpx.AsyncClient`` for the Bot API (tests inject a transport)."""
    return httpx.AsyncClient(transport=transport, timeout=60.0)