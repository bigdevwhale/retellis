"""Adapter registry — maps ``kind`` → ``MessengerAdapter`` instance.

Adding a messenger later = implement ``MessengerAdapter`` for it and add one
entry here. The poller and router look adapters up by ``kind``.
"""

from __future__ import annotations

from .base import MessengerAdapter
from .telegram.adapter import TelegramAdapter


def build_adapter_registry() -> dict[str, MessengerAdapter]:
    return {"telegram": TelegramAdapter()}


__all__ = ["build_adapter_registry"]