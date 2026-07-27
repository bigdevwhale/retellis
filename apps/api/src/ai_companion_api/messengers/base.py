"""The messenger-agnostic adapter surface.

Every external messenger (Telegram today; WhatsApp/Signal/Discord later)
implements ``MessengerAdapter``. The store and poller talk only to this
Protocol, so adding a new messenger = a new subpackage + one ``registry.py``
entry — no core changes.

The shapes here are deliberately plain dataclasses / Enums so adapters map
their vendor-specific JSON into a common form at the edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class AdapterError(Exception):
    """Base for adapter failures."""


class TokenInvalid(AdapterError):
    """The bot token was rejected by the messenger (401/404). Permanent."""


class TransientError(AdapterError):
    """A retryable transport/server error (5xx, timeout, connection)."""


class FatalError(AdapterError):
    """A non-retryable error that should stop the poller (e.g. 409 conflict,
    revoked token mid-flight). Distinct from ``TokenInvalid`` so the poller can
    decide whether to mark the row ``error`` vs delete it."""


@dataclass(frozen=True)
class AdapterInfo:
    """Result of ``validate_token`` (Telegram ``getMe``)."""

    bot_id: int
    username: str
    can_join_groups: bool = False


@dataclass(frozen=True)
class ButtonSpec:
    text: str
    callback_data: str


@dataclass
class AdapterUpdate:
    """One polled update normalized across messengers.

    ``text`` is the user's message text (None for non-text updates like a
    callback). ``command`` + ``args`` are parsed for slash-commands (Telegram
    ``/start <token>``); adapters that don't have commands leave them None.
    """

    update_id: int
    chat_id: int
    text: str | None = None
    command: str | None = None
    command_args: str | None = None
    callback_id: str | None = None
    callback_data: str | None = None
    raw: object = field(default=None, repr=False)


@runtime_checkable
class MessengerAdapter(Protocol):
    """External-messenger adapter. One instance serves all bots of this kind.

    The bot token is passed per call (the store holds it envelope-encrypted; the
    poller decrypts once per cycle). The adapter NEVER caches tokens.
    """

    kind: str  # "telegram" | "whatsapp" | ...

    async def validate_token(self, token: str) -> AdapterInfo: ...
    async def poll_once(
        self, token: str, offset: int | None, *, timeout: int = 30
    ) -> list[AdapterUpdate]: ...
    async def send_text(self, token: str, chat_id: int, text: str) -> None: ...
    async def send_typing(self, token: str, chat_id: int) -> None: ...
    async def send_inline(
        self, token: str, chat_id: int, text: str, buttons: list[list[ButtonSpec]]
    ) -> None: ...
    async def answer_callback(self, token: str, callback_id: str, text: str | None) -> None: ...


__all__ = [
    "AdapterError",
    "AdapterInfo",
    "AdapterUpdate",
    "ButtonSpec",
    "FatalError",
    "MessengerAdapter",
    "TokenInvalid",
    "TransientError",
]