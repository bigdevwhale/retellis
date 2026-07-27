"""User + session persistence for the auth layer.

Mirrors ``memory/store.py``: one ``AuthStore`` Protocol with an in-memory and a
Postgres implementation, picked by ``make_auth_store(settings)``. The in-memory
store is the zero-config default (tests, local, and graceful fallback when the DB
is unreachable); the Postgres store uses the shared async session factory from
``db.session`` so auth and memory share one engine.

The cookie holds only an opaque session *token* (a row in ``sessions``); the
master key / provider key / vault passphrase never enter this store. Sessions are
revocable (logout / "sign out everywhere" / breach response).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass
class UserRecord:
    id: str
    email: str | None
    display_name: str | None
    plan: str
    credits_usd: float
    # Argon2id hash (local backend) or None (OIDC / trusted-header / magic-link).
    password_hash: str | None
    # Identity link: (issuer, subject) uniquely identifies the user. For local,
    # issuer="local" and subject=email; for OIDC, issuer=issuer URL and subject=sub;
    # for trusted-header, issuer="trusted-header" and subject=header value.
    issuer: str
    subject: str
    # Family scope. NULL when the user is not in a family. Each user belongs to
    # at most one family; family_role is "owner" for the family-creating user
    # and "member" for the rest. Mutated by routers/family.py.
    family_id: str | None = None
    family_role: str | None = None
    created_at: datetime | None = None


@dataclass
class SessionRecord:
    # ``token`` is the cookie value — a SECRET. It stays in this record for the
    # auth layer's internal use (cookie read / revoke-by-token) but MUST NOT be
    # surfaced to the client. The session-list / revoke endpoints expose only
    # the opaque surrogate ``id`` (M2).
    token: str
    user_id: str
    expires_at: datetime
    revoked_at: datetime | None
    # M2: surrogate id (opaque to the client, never the token), plus the
    # "active devices" metadata. ``created_at``/``user_agent`` are None on
    # in-memory rows created before M2 and on backends that don't capture them.
    id: str | None = None
    created_at: datetime | None = None
    user_agent: str | None = None


@runtime_checkable
class AuthStore(Protocol):
    """Async user + session store. All methods are awaitable."""

    async def create_user(
        self,
        *,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        password_hash: str | None,
        plan: str,
        credits_usd: float,
    ) -> UserRecord: ...
    async def get_user(self, user_id: str) -> UserRecord | None: ...
    async def get_user_by_email(self, email: str) -> UserRecord | None: ...
    async def get_user_by_subject(self, *, issuer: str, subject: str) -> UserRecord | None: ...
    async def create_session(
        self, *, user_id: str, ttl_seconds: int, user_agent: str | None = None
    ) -> str: ...
    async def get_session(self, token: str) -> SessionRecord | None: ...
    async def revoke_session(self, token: str) -> bool: ...
    async def revoke_all_sessions(self, *, user_id: str, keep_token: str | None = None) -> int: ...
    async def list_sessions(self, *, user_id: str) -> list[SessionRecord]: ...
    async def revoke_session_by_id(self, *, user_id: str, session_id: str) -> bool: ...
    async def decrement_credits(self, *, user_id: str, amount: float) -> bool: ...
    # Billing: set the user's plan and ADDITIVELY top up credits_usd by the
    # plan's grant. Called from the billing webhook on a successful payment /
    # renewal. Additive (not a replace) so an early renewal or mid-cycle upgrade
    # doesn't burn the user's remaining balance. Returns False if the user
    # doesn't exist (caller treats as no-op; a webhook for a deleted user is
    # dropped silently).
    async def set_user_plan(self, *, user_id: str, plan: str, credits_grant_usd: float) -> bool: ...
    async def table_exists(self) -> bool: ...
    # Family scope. The setter NULLs the family membership (used by leave /
    # disband); the attach path is the family router's accept / create
    # endpoints which call set_user_family directly.
    async def set_user_family(
        self, *, user_id: str, family_id: str | None, family_role: str | None
    ) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryAuthStore:
    """Process-local auth store — zero-config default and test fixture."""

    def __init__(self) -> None:
        self._users_by_id: dict[str, UserRecord] = {}
        self._users_by_email: dict[str, UserRecord] = {}
        self._users_by_subject: dict[tuple[str, str], UserRecord] = {}
        self._sessions: dict[str, SessionRecord] = {}

    async def create_user(
        self,
        *,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        password_hash: str | None,
        plan: str,
        credits_usd: float,
    ) -> UserRecord:
        # Idempotent by (issuer, subject): a second login for the same identity
        # returns the existing row instead of creating a duplicate.
        existing = self._users_by_subject.get((issuer, subject))
        if existing is not None:
            return existing
        user = UserRecord(
            id=str(uuid.uuid4()),
            email=email,
            display_name=display_name,
            plan=plan,
            credits_usd=credits_usd,
            password_hash=password_hash,
            issuer=issuer,
            subject=subject,
            created_at=_utcnow(),
        )
        self._users_by_id[user.id] = user
        if user.email:
            self._users_by_email[user.email.lower()] = user
        self._users_by_subject[(issuer, subject)] = user
        return user

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self._users_by_id.get(user_id)

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        return self._users_by_email.get(email.lower())

    async def get_user_by_subject(self, *, issuer: str, subject: str) -> UserRecord | None:
        return self._users_by_subject.get((issuer, subject))

    async def create_session(
        self, *, user_id: str, ttl_seconds: int, user_agent: str | None = None
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = _utcnow()
        self._sessions[token] = SessionRecord(
            token=token,
            user_id=user_id,
            expires_at=now + timedelta(seconds=ttl_seconds),
            revoked_at=None,
            id=uuid.uuid4().hex,
            created_at=now,
            user_agent=user_agent,
        )
        return token

    async def get_session(self, token: str) -> SessionRecord | None:
        s = self._sessions.get(token)
        if s is None:
            return None
        if s.revoked_at is not None or s.expires_at <= _utcnow():
            return None
        return s

    async def revoke_session(self, token: str) -> bool:
        s = self._sessions.get(token)
        if s is None or s.revoked_at is not None:
            return False
        s.revoked_at = _utcnow()
        return True

    async def revoke_all_sessions(self, *, user_id: str, keep_token: str | None = None) -> int:
        n = 0
        for s in self._sessions.values():
            if s.user_id == user_id and s.revoked_at is None and s.token != keep_token:
                s.revoked_at = _utcnow()
                n += 1
        return n

    async def list_sessions(self, *, user_id: str) -> list[SessionRecord]:
        # Active sessions only (not revoked). Expired-but-not-revoked rows are
        # included so the UI can show "expired" — the wire layer can filter on
        # ``expires_at`` if it prefers to hide them.
        return [s for s in self._sessions.values() if s.user_id == user_id and s.revoked_at is None]

    async def revoke_session_by_id(self, *, user_id: str, session_id: str) -> bool:
        for s in self._sessions.values():
            if s.id == session_id and s.user_id == user_id and s.revoked_at is None:
                s.revoked_at = _utcnow()
                return True
        return False

    async def decrement_credits(self, *, user_id: str, amount: float) -> bool:
        u = self._users_by_id.get(user_id)
        if u is None or amount <= 0:
            return False
        # Atomic conditional: only debit when the balance covers it — prevents
        # going negative under concurrent turns (the in-memory store is
        # single-process so this is a plain check, but the contract matches the
        # Postgres conditional UPDATE).
        if u.credits_usd >= amount:
            u.credits_usd -= amount
            return True
        return False

    async def set_user_plan(self, *, user_id: str, plan: str, credits_grant_usd: float) -> bool:
        u = self._users_by_id.get(user_id)
        if u is None:
            return False
        u.plan = plan
        # Additive top-up — an early renewal or mid-cycle upgrade must not burn
        # the remaining balance. Matches the Postgres conditional UPDATE.
        if credits_grant_usd > 0:
            u.credits_usd += credits_grant_usd
        return True

    async def set_user_family(
        self, *, user_id: str, family_id: str | None, family_role: str | None
    ) -> None:
        u = self._users_by_id.get(user_id)
        if u is not None:
            u.family_id = family_id
            u.family_role = family_role

    async def table_exists(self) -> bool:
        return True  # in-memory always "ready"


class PostgresAuthStore:
    """SQLAlchemy auth store — used in ``docker compose`` (``COMPANION_USE_DB=1``).

    Shares the async engine from ``db.session`` with ``PostgresStore``. Falls back
    to in-memory at the factory level if the tables are missing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _session(self):
        from ..db.session import get_sessionmaker  # lazy: keep zero-config import path clean

        sm = get_sessionmaker(self._settings)
        return sm()

    async def create_user(
        self,
        *,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str | None,
        password_hash: str | None,
        plan: str,
        credits_usd: float,
    ) -> UserRecord:
        from ..db.models import Session as SessionModel  # noqa: F401  (ensure registry loaded)
        from ..db.models import User as UserModel

        existing = await self.get_user_by_subject(issuer=issuer, subject=subject)
        if existing is not None:
            return existing
        async with await self._session() as s:
            row = UserModel(
                id=str(uuid.uuid4()),
                email=email,
                display_name=display_name,
                plan=plan,
                credits_usd=credits_usd,
                password_hash=password_hash,
                issuer=issuer,
                subject=subject,
            )
            s.add(row)
            await s.commit()
            return _row_to_user(row)

    async def get_user(self, user_id: str) -> UserRecord | None:
        from ..db.models import User as UserModel

        async with await self._session() as s:
            row = await s.get(UserModel, user_id)
            return _row_to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        from sqlalchemy import func, select

        from ..db.models import User as UserModel

        # I18: ``ilike(email)`` treats ``%`` / ``_`` in the input as wildcards
        # — a signup/login with ``a%`` could match ``alice`` (enumeration +
        # collision). Use a case-insensitive equality on the lowercased email
        # instead, matching the in-memory store's ``email.lower()`` lookup and
        # the unique lower(email) index. No wildcards, no injection.
        async with await self._session() as s:
            r = await s.execute(
                select(UserModel).where(func.lower(UserModel.email) == email.lower())
            )
            row = r.scalar_one_or_none()
            return _row_to_user(row) if row is not None else None

    async def get_user_by_subject(self, *, issuer: str, subject: str) -> UserRecord | None:
        from sqlalchemy import select

        from ..db.models import User as UserModel

        async with await self._session() as s:
            r = await s.execute(
                select(UserModel).where(UserModel.issuer == issuer, UserModel.subject == subject)
            )
            row = r.scalar_one_or_none()
            return _row_to_user(row) if row is not None else None

    async def create_session(
        self, *, user_id: str, ttl_seconds: int, user_agent: str | None = None
    ) -> str:
        from ..db.models import Session as SessionModel

        token = secrets.token_urlsafe(32)
        async with await self._session() as s:
            row = SessionModel(
                token=token,
                user_id=user_id,
                expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
                user_agent=user_agent,
            )
            s.add(row)
            await s.commit()
            return token

    async def get_session(self, token: str) -> SessionRecord | None:
        from sqlalchemy import select

        from ..db.models import Session as SessionModel

        async with await self._session() as s:
            r = await s.execute(select(SessionModel).where(SessionModel.token == token))
            row = r.scalar_one_or_none()
            if row is None:
                return None
            if row.revoked_at is not None or row.expires_at <= _utcnow():
                return None
            return _row_to_session(row)

    async def revoke_session(self, token: str) -> bool:
        from sqlalchemy import update

        from ..db.models import Session as SessionModel

        async with await self._session() as s:
            # Session's primary key is `token` — return it to detect whether the
            # UPDATE matched a live row.
            r = await s.execute(
                update(SessionModel)
                .where(SessionModel.token == token, SessionModel.revoked_at.is_(None))
                .values(revoked_at=_utcnow())
                .returning(SessionModel.token)
            )
            await s.commit()
            return r.scalar_one_or_none() is not None

    async def revoke_all_sessions(self, *, user_id: str, keep_token: str | None = None) -> int:
        from sqlalchemy import update

        from ..db.models import Session as SessionModel

        async with await self._session() as s:
            stmt = update(SessionModel).where(
                SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)
            )
            if keep_token is not None:
                stmt = stmt.where(SessionModel.token != keep_token)
            r = await s.execute(stmt.values(revoked_at=_utcnow()).returning(SessionModel.token))
            await s.commit()
            return len(r.scalars().all())

    async def list_sessions(self, *, user_id: str) -> list[SessionRecord]:
        from sqlalchemy import select

        from ..db.models import Session as SessionModel

        async with await self._session() as s:
            r = await s.execute(
                select(SessionModel)
                .where(SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None))
                .order_by(SessionModel.created_at.desc())
            )
            return [_row_to_session(row) for row in r.scalars().all()]

    async def revoke_session_by_id(self, *, user_id: str, session_id: str) -> bool:
        from sqlalchemy import update

        from ..db.models import Session as SessionModel

        async with await self._session() as s:
            # Keyed by the surrogate ``id`` AND ``user_id`` — the id is opaque to
            # the client but a cross-user revoke must still fail (no row matches
            # both predicates), so one user cannot revoke another's session.
            r = await s.execute(
                update(SessionModel)
                .where(
                    SessionModel.id == session_id,
                    SessionModel.user_id == user_id,
                    SessionModel.revoked_at.is_(None),
                )
                .values(revoked_at=_utcnow())
                .returning(SessionModel.token)
            )
            await s.commit()
            return r.scalar_one_or_none() is not None

    async def decrement_credits(self, *, user_id: str, amount: float) -> bool:
        if amount <= 0:
            return False
        from sqlalchemy import update

        from ..db.models import User as UserModel

        async with await self._session() as s:
            # Atomic conditional debit: a single UPDATE that only fires when the
            # balance covers the amount, RETURNING the new balance. No row
            # returned ⇒ insufficient funds (caller treats as no-debit). This
            # closes the double-debit race the old two-UPDATE version had under
            # concurrent turns (two turns both saw credits >= amount, both
            # debited, balance went negative and was clamped). The residual
            # TOCTOU between the pre-turn gate and this debit is documented in
            # the Sprint 7 risks — a per-user asyncio lock fully closes it.
            r = await s.execute(
                update(UserModel)
                .where(UserModel.id == user_id, UserModel.credits_usd >= amount)
                .values(credits_usd=UserModel.credits_usd - amount)
                .returning(UserModel.credits_usd)
            )
            await s.commit()
            return r.scalar_one_or_none() is not None

    async def set_user_plan(self, *, user_id: str, plan: str, credits_grant_usd: float) -> bool:
        from sqlalchemy import update

        from ..db.models import User as UserModel

        async with await self._session() as s:
            # Atomic plan + additive credit top-up in one UPDATE. RETURNING the
            # id so a webhook for a since-deleted user is a no-op (False) — we
            # never grant credits to a tombstoned account. Additive on
            # credits_usd so an early renewal / mid-cycle upgrade doesn't burn
            # the remaining balance.
            r = await s.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(
                    plan=plan,
                    credits_usd=UserModel.credits_usd + credits_grant_usd,
                )
                .returning(UserModel.id)
            )
            await s.commit()
            return r.scalar_one_or_none() is not None

    async def set_user_family(
        self, *, user_id: str, family_id: str | None, family_role: str | None
    ) -> None:
        from sqlalchemy import update

        from ..db.models import User as UserModel

        async with await self._session() as s:
            await s.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(family_id=family_id, family_role=family_role)
            )
            await s.commit()

    async def table_exists(self) -> bool:
        from sqlalchemy import select

        from ..db.models import User as UserModel  # noqa: F401  (ensure registry loaded)

        async with await self._session() as s:
            try:
                await s.execute(select(UserModel).limit(1))
                return True
            except Exception:  # noqa: BLE001 — degrade gracefully, like make_store
                logger.warning("auth tables missing — falling back to in-memory auth store")
                return False


def _row_to_user(row) -> UserRecord:  # type: ignore[no-untyped-def]
    return UserRecord(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        plan=row.plan,
        credits_usd=row.credits_usd,
        password_hash=row.password_hash,
        issuer=row.issuer,
        subject=row.subject,
        family_id=row.family_id,
        family_role=row.family_role,
        created_at=row.created_at,
    )


def _row_to_session(row) -> SessionRecord:  # type: ignore[no-untyped-def]
    return SessionRecord(
        token=row.token,
        user_id=row.user_id,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        id=row.id,
        created_at=row.created_at,
        user_agent=row.user_agent,
    )


def make_auth_store(settings: Settings) -> AuthStore:
    """Pick the auth store by ``COMPANION_USE_DB``. Falls back to in-memory so the
    API never fails to boot (auth is process-local in that case — sessions do not
    persist across restarts, same trade-off as the memory store)."""
    if not settings.use_db:
        return InMemoryAuthStore()
    return PostgresAuthStore(settings)


__all__ = [
    "AuthStore",
    "InMemoryAuthStore",
    "PostgresAuthStore",
    "SessionRecord",
    "UserRecord",
    "make_auth_store",
]
