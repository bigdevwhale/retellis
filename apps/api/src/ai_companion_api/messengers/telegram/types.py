"""Typed shapes for Telegram Bot API ``Update`` objects.

The Bot API returns loosely-typed JSON; these dataclasses normalize the
fields the adapter/poller/commands actually use. Unknown fields are dropped
(the raw dict is kept on ``AdapterUpdate.raw`` when needed for debugging).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TgUser:
    id: int
    is_bot: bool
    username: str | None
    first_name: str | None


@dataclass(frozen=True)
class TgChat:
    id: int
    type: str  # "private" | "group" | ...
    username: str | None
    title: str | None


@dataclass(frozen=True)
class TgMessage:
    message_id: int
    from_user: TgUser | None
    chat: TgChat
    text: str | None
    date: int | None


@dataclass(frozen=True)
class TgCallbackQuery:
    id: str
    from_user: TgUser | None
    data: str | None
    message: TgMessage | None


@dataclass(frozen=True)
class TgUpdate:
    update_id: int
    message: TgMessage | None
    callback_query: TgCallbackQuery | None


def _user(d: object) -> TgUser | None:
    if not isinstance(d, dict):
        return None
    return TgUser(
        id=int(d.get("id", 0)),
        is_bot=bool(d.get("is_bot", False)),
        username=d.get("username"),
        first_name=d.get("first_name"),
    )


def _chat(d: object) -> TgChat | None:
    if not isinstance(d, dict):
        return None
    return TgChat(
        id=int(d.get("id", 0)),
        type=str(d.get("type", "")),
        username=d.get("username"),
        title=d.get("title"),
    )


def _message(d: object) -> TgMessage | None:
    if not isinstance(d, dict):
        return None
    chat = _chat(d.get("chat"))
    if chat is None:
        return None
    return TgMessage(
        message_id=int(d.get("message_id", 0)),
        from_user=_user(d.get("from")),
        chat=chat,
        text=d.get("text"),
        date=d.get("date"),
    )


def _callback(d: object) -> TgCallbackQuery | None:
    if not isinstance(d, dict):
        return None
    return TgCallbackQuery(
        id=str(d.get("id", "")),
        from_user=_user(d.get("from")),
        data=d.get("data"),
        message=_message(d.get("message")),
    )


def parse_update(raw: dict) -> TgUpdate:
    """Parse one Telegram ``Update`` dict. Tolerates missing fields."""
    return TgUpdate(
        update_id=int(raw.get("update_id", 0)),
        message=_message(raw.get("message")),
        callback_query=_callback(raw.get("callback_query")),
    )


__all__ = [
    "TgCallbackQuery",
    "TgChat",
    "TgMessage",
    "TgUpdate",
    "TgUser",
    "parse_update",
]