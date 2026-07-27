"""Family-invite signing secret — auto-generated + persisted on first boot.

The family-invite tokens (``POST /v1/family/invites`` → email link →
``POST /v1/family/accept``) are HMAC-SHA256 signed by
``auth.sessions.seal``/``open_sealed``. ``open_sealed`` rejects any token
signed with an empty secret, so the family invite flow is dead-on-arrival
when neither ``AUTH_INVITE_SECRET`` nor ``AUTH_STATE_SECRET`` is set in the
deployment env (the default for the local ``self_hosted+local`` profile —
OIDC and magic-link are off, and those two env vars are documented only in
those backends' sections of ``.env.example``).

In compose this manifests as ``POST /v1/family/accept`` → 400
``{"detail":"invalid invite token"}`` and the user never gets attached to
the family, which then cascades to ``GET /v1/family`` → 404
``{"detail":"not in a family"}``.

The fix: when the operator hasn't set an explicit secret, generate a
32-byte URL-safe random secret on first boot and persist it to
``<COMPANION_INVITE_SECRET_FILE>`` (default ``/var/lib/companion/invite_secret``).
The file is mode ``0600``; the directory is created with mode ``0700`` on
first use. The volume is mounted in ``deploy/docker-compose.yml`` so the
key survives container restarts and the same invite tokens stay verifiable.

In dev (``uvicorn`` outside Docker), the file is written next to the repo
under ``.runtime/invite_secret`` — also ``0600`` — so a developer's
invites keep working across reloads. The user can still override either
way by setting ``AUTH_INVITE_SECRET`` (or ``AUTH_STATE_SECRET``) in env.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_FILE = "/var/lib/companion/invite_secret"
_DEV_SECRET_FILE = ".runtime/invite_secret"


def _default_path() -> str:
    # In Docker, /var/lib/companion is the canonical persistent path; in dev
    # (no /var/lib/companion write access) we drop it under the repo's
    # .runtime/ so it's gitignored and survives reloads.
    if os.path.isdir("/var/lib/companion") and os.access("/var/lib/companion", os.W_OK):
        return _DEFAULT_SECRET_FILE
    return _DEV_SECRET_FILE


def ensure_invite_secret(*, settings, env_path: str | None = None) -> str:
    """Return the invite secret, generating + persisting one if neither
    ``settings.auth_invite_secret`` nor ``settings.auth_state_secret`` is set.

    Order of precedence (highest first):
      1. ``settings.auth_invite_secret`` (env ``AUTH_INVITE_SECRET``)
      2. ``settings.auth_state_secret``   (env ``AUTH_STATE_SECRET``)
      3. The persisted file at ``COMPANION_INVITE_SECRET_FILE`` (read once).
      4. Newly generated + persisted.

    The chosen value is written back to ``settings.auth_invite_secret`` so
    downstream callers (``routers/family._invite_secret``) see it without an
    extra hop.
    """
    if settings.auth_invite_secret:
        return settings.auth_invite_secret
    if settings.auth_state_secret:
        return settings.auth_state_secret

    path = Path(env_path or os.environ.get("COMPANION_INVITE_SECRET_FILE") or _default_path())
    if path.exists():
        try:
            val = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.error("invite secret: read %s failed: %s — regenerating", path, e)
            val = ""
        if val:
            settings.auth_invite_secret = val
            logger.info("invite secret: loaded from %s", path)
            return val

    # Generate. ``secrets.token_urlsafe(32)`` = 256 bits of entropy, URL-safe
    # alphabet, no escaping needed for any env/file transport.
    val = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0o700 dir + 0o600 file: only the API user can read the secret.
        if path.parent.exists():
            try:
                path.parent.chmod(stat.S_IRWXU)
            except OSError:
                pass
        path.write_text(val + "\n", encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    except OSError as e:
        # Non-fatal: the in-process value is still valid for this lifetime.
        # Restarts will regenerate (and invalidate outstanding invites).
        logger.error("invite secret: persist to %s failed: %s", path, e)
    settings.auth_invite_secret = val
    logger.info("invite secret: generated (persisted to %s)", path)
    return val


__all__ = ["ensure_invite_secret"]
