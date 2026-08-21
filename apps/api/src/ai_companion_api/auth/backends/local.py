"""Local accounts backend — Argon2id passwords, zero external dependencies.

The default for ``self_hosted + local`` profile, and also allowed under ``hosted``
for an operator who wants a hosted SaaS with its own email+password registration
(pair with ``FEATURE_EMAIL_VERIFICATION`` so signups confirm email ownership).
No IdP, no callback URL. Reuses the libsodium/Argon2id stack already imported for
the vault so crypto is consistent and no new dependency is added. The login
password is distinct from the vault passphrase — the server hashes it and never
sees the passphrase (which stays in the browser).
"""

from __future__ import annotations

from ai_companion_contracts import Principal

from ...config import Settings
from ..store import AuthStore, UserRecord
from .base import AuthError

# PyNaCl is already a dependency (vault). argon2id.str / verify are the high-level
# PHC-string helpers — they pick libsodium's recommended opslimit/memlimit.
try:
    from nacl import pwhash

    def _hash_password(password: str) -> str:
        return pwhash.argon2id.str(password.encode("utf-8")).decode("utf-8")

    def _verify_password(stored: str, password: str) -> bool:
        try:
            pwhash.argon2id.verify(stored.encode("utf-8"), password.encode("utf-8"))
            return True
        except Exception:  # noqa: BLE001 — any verify failure = bad credentials
            return False

except Exception:  # pragma: no cover — nacl is expected present in the venv
    # Fallback so import never hard-fails in a stripped env; tests that need real
    # hashing run where nacl is installed.
    def _hash_password(password: str) -> str:
        return f"noop:{password}"

    def _verify_password(stored: str, password: str) -> bool:
        return stored == f"noop:{password}"


def _default_plan(settings: Settings) -> str:
    # Hosted local-signup users get the hosted free tier (the credits/budget
    # gates key on this); self-hosted stays on the self-hosted free plan.
    return "hosted_free" if settings.deployment_mode == "hosted" else "self_hosted_free"


class LocalAccountsBackend:
    name = "local"

    def __init__(self, settings: Settings, store: AuthStore) -> None:
        self.settings = settings
        self.store = store

    async def signup(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
        user_agent: str | None = None,
        lang: str | None = None,
    ) -> tuple[UserRecord, str]:
        email = email.strip().lower()
        if not email or not password:
            raise AuthError(400, "email and password are required")
        if await self.store.get_user_by_email(email) is not None:
            raise AuthError(409, "an account with that email already exists")
        # Soft email verification: when the flag is on, the new account starts
        # unverified and a verification link is emailed. The session is still
        # issued here (soft — the user can use the app while unverified); the
        # banner + resend live in the frontend. Flag off → email_verified=True
        # (default), identical to the pre-verification behavior.
        verify_on = self.settings.feature_email_verification
        hosted = self.settings.deployment_mode == "hosted"
        user = await self.store.create_user(
            issuer="local",
            subject=email,
            email=email,
            display_name=display_name or email,
            password_hash=_hash_password(password),
            plan=_default_plan(self.settings),
            credits_usd=self.settings.hosted_signup_credits_usd if hosted else 0.0,
            email_verified=not verify_on,
        )
        token = await self.store.create_session(
            user_id=user.id,
            ttl_seconds=self.settings.auth_session_ttl_seconds,
            user_agent=user_agent,
        )
        if verify_on:
            # Best-effort: a transient SMTP failure must not break the signup —
            # the account + session already exist and the user can resend from
            # the banner. Log so a silent "no verification email" is diagnosable.
            from ..email_verification import send_verification_email

            try:
                await send_verification_email(self.settings, self.store, email, lang=lang)
            except Exception:  # noqa: BLE001 — never fail signup on email send
                import logging

                logging.getLogger(__name__).warning(
                    "verification email send failed for signup %s; user can resend",
                    email,
                )
        return user, token

    async def login(
        self, *, email: str, password: str, user_agent: str | None = None
    ) -> tuple[UserRecord, str]:
        email = email.strip().lower()
        user = await self.store.get_user_by_email(email)
        if (
            user is None
            or not user.password_hash
            or not _verify_password(user.password_hash, password)
        ):
            # Same message for "no such user" and "bad password" — no user enumeration.
            raise AuthError(401, "invalid email or password")
        token = await self.store.create_session(
            user_id=user.id,
            ttl_seconds=self.settings.auth_session_ttl_seconds,
            user_agent=user_agent,
        )
        return user, token

    async def resolve(self, request) -> Principal | None:  # noqa: ANN001
        # Session-based: the middleware resolves the cookie → session → Principal.
        return None


__all__ = ["LocalAccountsBackend"]
