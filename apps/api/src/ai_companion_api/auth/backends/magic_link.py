"""Magic-link backend — signed, short-lived email login links.

A link like ``{public_origin}/v1/auth/magiclink/verify?token=…`` is emailed to the
user; clicking it signs them in. The token is a sealed payload
(``seal({email, exp, nonce}, secret)``) — signed with ``AUTH_MAGIC_LINK_SECRET`` and
verifiable without server-side state (multi-process safe). Used by ``hosted`` and
optionally by ``self_hosted + sso`` (requires SMTP).

Email transport is pluggable: ``console`` (prints the link — local/dev default),
``smtp`` (smtplib), or ``off`` (disabled; ``bootstrap`` rejects magic_link with
``off``). Tests inject a capture transport.

Honest note: within its TTL a token may be used to log in more than once (it is a
login link, not a one-time secret) — acceptable for MVP; a consumed-token table is
a hardening follow-up.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from ...config import Settings
from ..sessions import open_sealed, seal
from ..store import AuthStore, UserRecord
from .base import AuthError

logger = logging.getLogger(__name__)

MAGIC_LINK_TTL_SECONDS = 15 * 60  # 15 minutes


class EmailTransport(Protocol):
    async def send(self, *, to: str, link: str) -> None: ...


class ConsoleEmailTransport:
    """Default for local/dev: print the magic link so the operator can click it."""

    async def send(self, *, to: str, link: str) -> None:
        # Log ONLY the recipient — never the link. The link carries the sealed
        # ``?token=…`` login credential, which has no ``sk-``/``AIza``/``Bearer``
        # shape and so sails past ``redaction.RedactingFilter``. Structured logs
        # (and Langfuse metadata) must not contain a login token. The ``print``
        # below is the explicit dev affordance that makes the console transport
        # usable at all (click the link to sign in); it is dev-only and never
        # installed in hosted SMTP deployments.
        logger.info("magic-link issued for %s (sent via console transport)", to)
        print(f"[magic-link] {to} -> {link}", flush=True)


class SMTPEmailTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, *, to: str, link: str) -> None:
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = "Your Stillside sign-in link"
        msg["From"] = s.smtp_from or "noreply@stillside.local"
        msg["To"] = to
        msg.set_content(
            f"Click to sign in to Stillside:\n\n{link}\n\nThis link expires in 15 minutes."
        )
        # smtplib is blocking; run in a thread to avoid stalling the event loop.
        import asyncio

        def _send() -> None:
            ctx = ssl.create_default_context() if s.smtp_port == 465 else None
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_port != 465:
                    smtp.starttls(context=ctx)
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                smtp.send_message(msg)

        await asyncio.to_thread(_send)


class _OffTransport:
    async def send(self, *, to: str, link: str) -> None:
        raise AuthError(503, "email transport is off; cannot send magic link")


def default_transport(settings: Settings) -> EmailTransport:
    t = settings.auth_email_transport
    if t == "smtp":
        return SMTPEmailTransport(settings)
    if t == "off":
        return _OffTransport()
    return ConsoleEmailTransport()


class MagicLinkBackend:
    name = "magic_link"

    def __init__(
        self,
        settings: Settings,
        store: AuthStore,
        transport: EmailTransport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.transport = transport or default_transport(settings)

    def _issue_token(self, email: str) -> str:
        import secrets as _secrets
        import time

        payload = {
            "email": email,
            "exp": int(time.time()) + MAGIC_LINK_TTL_SECONDS,
            "nonce": _secrets.token_urlsafe(8),
        }
        return seal(payload, self.settings.auth_magic_link_secret)

    def verify_url(self, token: str) -> str:
        return f"{self.settings.public_origin.rstrip('/')}/v1/auth/magiclink/verify?token={token}"

    async def send(self, *, email: str) -> str:
        email = email.strip().lower()
        if not email:
            raise AuthError(400, "email is required")
        token = self._issue_token(email)
        await self.transport.send(to=email, link=self.verify_url(token))
        return token

    async def verify(self, token: str, *, user_agent: str | None = None) -> tuple[UserRecord, str]:
        payload = open_sealed(token, self.settings.auth_magic_link_secret)
        if payload is None:
            raise AuthError(400, "invalid or tampered magic link")
        import time

        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthError(400, "magic link has expired")
        email = str(payload.get("email", "")).strip().lower()
        if not email:
            raise AuthError(400, "magic link missing email")
        plan = "hosted_free" if self.settings.deployment_mode == "hosted" else "self_hosted_free"
        credits = self.settings.hosted_signup_credits_usd if plan != "self_hosted_free" else 0.0
        user = await self.store.create_user(
            issuer="magic-link",
            subject=email,
            email=email,
            display_name=email,
            password_hash=None,
            plan=plan,
            credits_usd=credits,
        )
        session_token = await self.store.create_session(
            user_id=user.id,
            ttl_seconds=self.settings.auth_session_ttl_seconds,
            user_agent=user_agent,
        )
        return user, session_token

    async def resolve(self, request) -> None:  # noqa: ANN001
        return None


__all__ = [
    "ConsoleEmailTransport",
    "EmailTransport",
    "MagicLinkBackend",
    "SMTPEmailTransport",
]
