"""Persistence for per-user messenger links (the ``messengers`` table).

Mirrors ``auth/store.py``: one ``MessengerStore`` Protocol with an in-memory
and a Postgres implementation, picked by ``make_messenger_store(settings)``.
The in-memory store is the zero-config default (tests + graceful fallback);
the Postgres store shares the async engine from ``db.session``.

Security notes:

- ``bot_token_ciphertext`` and ``byok_enc_blob`` are envelope ciphertext
  (``crypto/envelope.py``) — base64 text. Plaintext bot tokens never enter
  this store, and ``MessengerRecord`` never carries them.
- ``bot_token_masked`` (last 4 chars) is the only token-derived string the API
  may surface.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass
class MessengerRecord:
    id: str
    user_id: str
    kind: str  # "telegram" today; whatsapp/signal later
    status: str  # pending_handshake | active | paused | error
    persona_id: str
    # Envelope ciphertext (base64). Never plaintext — see module docstring.
    bot_token_ciphertext: str
    byok_enc_blob: str | None = None
    chat_id: int | None = None
    bot_username: str | None = None
    bot_token_masked: str = ""
    last_error: str | None = None
    last_seen_at: datetime | None = None
    next_offset: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


@runtime_checkable
class MessengerStore(Protocol):
    """Async messenger store. All methods are awaitable."""

    async def create(
        self,
        *,
        user_id: str,
        kind: str,
        bot_token_ciphertext: str,
        bot_token_masked: str,
        persona_id: str,
        status: str = "pending_handshake",
    ) -> MessengerRecord: ...
    async def get(self, messenger_id: str) -> MessengerRecord | None: ...
    async def get_for_user(self, messenger_id: str, user_id: str) -> MessengerRecord | None: ...
    async def list_by_user(self, user_id: str) -> list[MessengerRecord]: ...
    async def list_active(self) -> list[MessengerRecord]: ...
    async def update(self, messenger_id: str, **fields: object) -> MessengerRecord | None: ...
    async def delete(self, messenger_id: str) -> bool: ...
    async def table_exists(self) -> bool: ...


# Columns ``update()`` is allowed to touch. Guards against typos silently
# writing nothing (and against a caller ever setting ``id``/``user_id``).
_UPDATABLE = {
    "status",
    "persona_id",
    "bot_token_ciphertext",
    "byok_enc_blob",
    "chat_id",
    "bot_username",
    "bot_token_masked",
    "last_error",
    "last_seen_at",
    "next_offset",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryMessengerStore:
    """Process-local store — zero-config default and test fixture."""

    def __init__(self) -> None:
        self._by_id: dict[str, MessengerRecord] = {}

    async def create(
        self,
        *,
        user_id: str,
        kind: str,
        bot_token_ciphertext: str,
        bot_token_masked: str,
        persona_id: str,
        status: str = "pending_handshake",
    ) -> MessengerRecord:
        # One bot per (user, kind) in the MVP — mirrors the DB unique
        # constraint. A second create for the same pair returns the existing row
        # so a double-clicked init doesn't duplicate (idempotent).
        for m in self._by_id.values():
            if m.user_id == user_id and m.kind == kind:
                return m
        now = _utcnow()
        record = MessengerRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            kind=kind,
            status=status,
            persona_id=persona_id,
            bot_token_ciphertext=bot_token_ciphertext,
            bot_token_masked=bot_token_masked,
            created_at=now,
            updated_at=now,
        )
        self._by_id[record.id] = record
        return record

    async def get(self, messenger_id: str) -> MessengerRecord | None:
        return self._by_id.get(messenger_id)

    async def get_for_user(self, messenger_id: str, user_id: str) -> MessengerRecord | None:
        m = self._by_id.get(messenger_id)
        return m if m is not None and m.user_id == user_id else None

    async def list_by_user(self, user_id: str) -> list[MessengerRecord]:
        return [m for m in self._by_id.values() if m.user_id == user_id]

    async def list_active(self) -> list[MessengerRecord]:
        return [m for m in self._by_id.values() if m.status == "active"]

    async def update(self, messenger_id: str, **fields: object) -> MessengerRecord | None:
        m = self._by_id.get(messenger_id)
        if m is None:
            return None
        for key, value in fields.items():
            if key not in _UPDATABLE:
                raise ValueError(f"messenger field {key!r} is not updatable")
            setattr(m, key, value)
        if fields:
            m.updated_at = _utcnow()
        return m

    async def delete(self, messenger_id: str) -> bool:
        return self._by_id.pop(messenger_id, None) is not None

    async def table_exists(self) -> bool:
        return True


class PostgresMessengerStore:
    """SQLAlchemy store — used in ``docker compose`` (``COMPANION_USE_DB=1``)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _session(self):
        from ..db.session import get_sessionmaker  # lazy: keep zero-config import path clean

        sm = get_sessionmaker(self._settings)
        return sm()

    async def create(
        self,
        *,
        user_id: str,
        kind: str,
        bot_token_ciphertext: str,
        bot_token_masked: str,
        persona_id: str,
        status: str = "pending_handshake",
    ) -> MessengerRecord:
        from sqlalchemy import select

        from ..db.models import Messenger as MessengerModel

        async with await self._session() as s:
            r = await s.execute(
                select(MessengerModel).where(
                    MessengerModel.user_id == user_id, MessengerModel.kind == kind
                )
            )
            existing = r.scalar_one_or_none()
            if existing is not None:
                return _row_to_messenger(existing)
            row = MessengerModel(
                id=str(uuid.uuid4()),
                user_id=user_id,
                kind=kind,
                status=status,
                persona_id=persona_id,
                bot_token_ciphertext=bot_token_ciphertext,
                bot_token_masked=bot_token_masked,
            )
            s.add(row)
            await s.commit()
            return _row_to_messenger(row)

    async def get(self, messenger_id: str) -> MessengerRecord | None:
        from ..db.models import Messenger as MessengerModel

        async with await self._session() as s:
            row = await s.get(MessengerModel, messenger_id)
            return _row_to_messenger(row) if row is not None else None

    async def get_for_user(self, messenger_id: str, user_id: str) -> MessengerRecord | None:
        from sqlalchemy import select

        from ..db.models import Messenger as MessengerModel

        # Keyed by id AND user_id — same cross-user contract as the rest of the
        # API (a cross-scope id looks like a missing one, 404 not 403).
        async with await self._session() as s:
            r = await s.execute(
                select(MessengerModel).where(
                    MessengerModel.id == messenger_id, MessengerModel.user_id == user_id
                )
            )
            row = r.scalar_one_or_none()
            return _row_to_messenger(row) if row is not None else None

    async def list_by_user(self, user_id: str) -> list[MessengerRecord]:
        from sqlalchemy import select

        from ..db.models import Messenger as MessengerModel

        async with await self._session() as s:
            r = await s.execute(
                select(MessengerModel)
                .where(MessengerModel.user_id == user_id)
                .order_by(MessengerModel.created_at)
            )
            return [_row_to_messenger(row) for row in r.scalars().all()]

    async def list_active(self) -> list[MessengerRecord]:
        from sqlalchemy import select

        from ..db.models import Messenger as MessengerModel

        async with await self._session() as s:
            r = await s.execute(select(MessengerModel).where(MessengerModel.status == "active"))
            return [_row_to_messenger(row) for row in r.scalars().all()]

    async def update(self, messenger_id: str, **fields: object) -> MessengerRecord | None:
        from sqlalchemy import update

        from ..db.models import Messenger as MessengerModel

        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"messenger field(s) not updatable: {sorted(bad)}")
        async with await self._session() as s:
            await s.execute(
                update(MessengerModel)
                .where(MessengerModel.id == messenger_id)
                .values(**fields, updated_at=_utcnow())
            )
            await s.commit()
            row = await s.get(MessengerModel, messenger_id)
            return _row_to_messenger(row) if row is not None else None

    async def delete(self, messenger_id: str) -> bool:
        from sqlalchemy import delete

        from ..db.models import Messenger as MessengerModel

        async with await self._session() as s:
            r = await s.execute(
                delete(MessengerModel)
                .where(MessengerModel.id == messenger_id)
                .returning(MessengerModel.id)
            )
            await s.commit()
            return r.scalar_one_or_none() is not None

    async def table_exists(self) -> bool:
        from sqlalchemy import select

        from ..db.models import Messenger as MessengerModel  # noqa: F401  (ensure registry loaded)

        async with await self._session() as s:
            try:
                await s.execute(select(MessengerModel).limit(1))
                return True
            except Exception:  # noqa: BLE001 — degrade gracefully, like make_store
                logger.warning(
                    "messengers table missing — falling back to in-memory messenger store"
                )
                return False


def _row_to_messenger(row) -> MessengerRecord:  # type: ignore[no-untyped-def]
    return MessengerRecord(
        id=row.id,
        user_id=row.user_id,
        kind=row.kind,
        status=row.status,
        persona_id=row.persona_id,
        bot_token_ciphertext=row.bot_token_ciphertext,
        byok_enc_blob=row.byok_enc_blob,
        chat_id=row.chat_id,
        bot_username=row.bot_username,
        bot_token_masked=row.bot_token_masked,
        last_error=row.last_error,
        last_seen_at=row.last_seen_at,
        next_offset=row.next_offset,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def make_messenger_store(settings: Settings) -> MessengerStore:
    """Pick the store by ``COMPANION_USE_DB``. Falls back to in-memory so the API
    never fails to boot (messenger links are process-local in that case)."""
    if not settings.use_db:
        return InMemoryMessengerStore()
    return PostgresMessengerStore(settings)


__all__ = [
    "InMemoryMessengerStore",
    "MessengerRecord",
    "MessengerStore",
    "PostgresMessengerStore",
    "make_messenger_store",
]
