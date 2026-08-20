"""Email verification for local-account signup.

Soft, opt-in (``FEATURE_EMAIL_VERIFICATION``) ownership proof for the local
backend: on signup the user starts ``email_verified=false`` and is emailed a
signed link; clicking it flips the flag. The session is issued immediately
(soft — the user can use the app while unverified), so this establishes email
ownership for *future* flows (forgot-password), it does not lock the user out.

Reuses the magic-link primitives so nothing new is invented:
- ``seal``/``open_sealed`` (HMAC-SHA256) from ``auth/sessions`` — same signing
  scheme as magic-link tokens, with a separate TTL and a separate (or shared)
  secret. The secret resolves to ``auth_email_verification_secret`` falling back
  to ``auth_magic_link_secret`` so an operator who set one secret is covered.
- ``default_transport(settings)`` from ``auth/backends/magic_link`` — the same
  console/smtp/off pluggable transport. Bootstrap requires smtp when the flag is
  on, so production sends real mail; tests inject a capture transport.

Honest note: the sealed token carries no ``sk-``/``Bearer`` shape and sails past
``redaction.RedactingFilter``, so we log ONLY the recipient email — never the
token or the link (same discipline as the magic-link console transport).
"""

from __future__ import annotations

import logging
import secrets as _secrets
import time

from ..config import Settings
from .backends import magic_link
from .backends.magic_link import EmailTransport
from .sessions import open_sealed, seal
from .store import AuthStore

logger = logging.getLogger(__name__)

VERIFY_SUBJECT = "Confirm your Retellis email"


def _secret(settings: Settings) -> str:
    """Verification signing secret. Falls back to the magic-link secret so an
    operator who configured only one secret is covered."""
    return settings.auth_email_verification_secret or settings.auth_magic_link_secret


def issue_token(settings: Settings, email: str) -> str:
    """Seal a verification token for ``email`` with the configured TTL."""
    payload = {
        "email": email.strip().lower(),
        "exp": int(time.time()) + settings.auth_email_verification_ttl_seconds,
        "nonce": _secrets.token_urlsafe(8),
    }
    return seal(payload, _secret(settings))


def verify_token(settings: Settings, token: str) -> str | None:
    """Open + expiry-check a verification token → lowercased email, or None on
    tamper / expiry / bad shape."""
    payload = open_sealed(token, _secret(settings))
    if payload is None:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    email = str(payload.get("email", "")).strip().lower()
    return email or None


def verify_url(settings: Settings, token: str) -> str:
    return f"{settings.public_origin.rstrip('/')}/v1/auth/verify-email?token={token}"


async def send_verification_email(
    settings: Settings,
    store: AuthStore,
    email: str,
    *,
    transport: EmailTransport | None = None,
) -> None:
    """Email a verification link to ``email`` if (and only if) a local account
    exists for it and is not yet verified. No-op otherwise — keeps the resend
    endpoint non-enumerating (unknown email ⇒ same ``{ok:true}`` as a known
    one, and no mail is sent to a stranger). Never raises on user state; a
    transport failure (e.g. SMTP down) does propagate to the caller so the
    signup/resend path surfaces it."""
    email = email.strip().lower()
    if not email:
        return
    user = await store.get_user_by_email(email)
    # No account, or already verified, or not a local account → silently skip.
    # The local-only guard avoids verifying a magic-link/OIDC identity through
    # this path (those backends prove email ownership their own way).
    if user is None or user.email_verified or user.issuer != "local":
        return
    token = issue_token(settings, email)
    link = verify_url(settings, token)
    # Resolved via the module attribute so tests can monkeypatch
    # ``magic_link.default_transport`` (same pattern as the family-invite flow).
    t = transport or magic_link.default_transport(settings)
    # Log ONLY the recipient — the link carries the sealed token (no sk-/Bearer
    # shape, so it would sail past redaction). Same discipline as the console
    # magic-link transport.
    logger.info("verification email issued for %s", email)
    await t.send(to=email, link=link, subject=VERIFY_SUBJECT)


__all__ = [
    "VERIFY_SUBJECT",
    "issue_token",
    "send_verification_email",
    "verify_token",
    "verify_url",
]