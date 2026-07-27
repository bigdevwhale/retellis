"""Connect-token: signs the deep-link handshake between Telegram ``/start``
and the web ``/connect/telegram`` page.

The init endpoint mints a short-lived signed token carrying the messenger id +
an expiry. The user pastes ``/start <token>`` to the bot; the poller verifies
the signature + TTL and replies with a deep link to
``{public_origin}/connect/telegram?messenger=<id>&token=<token>``. The web
page POSTs the same token to ``/v1/messengers/telegram/{id}/bind`` — the bind
endpoint verifies it again, so a token that never went through Telegram can
still bind (e.g. the user clicks the link in Settings directly).

Reuses ``auth.sessions.seal`` / ``open_sealed`` (HMAC-SHA256, tamper-evident,
multi-process safe — no server-side state). The secret is
``auth_state_secret`` (falls back to ``auth_invite_secret``).
"""

from __future__ import annotations

from typing import Any

from ..auth.sessions import open_sealed, seal


def _secret(settings) -> str:  # type: ignore[no-untyped-def]
    return settings.auth_state_secret or settings.auth_invite_secret


def issue_connect_token(*, messenger_id: str, settings, ttl_seconds: int | None = None) -> str:
    """Mint a signed connect token for ``messenger_id``."""
    from datetime import UTC, datetime, timedelta

    if ttl_seconds is None:
        ttl_seconds = settings.messenger_connect_token_ttl_seconds
    exp = int((datetime.now(UTC) + timedelta(seconds=ttl_seconds)).timestamp())
    return seal({"mid": messenger_id, "exp": exp}, _secret(settings))


def verify_connect_token(token: str, *, messenger_id: str, settings) -> bool:
    """True iff ``token`` is signature-valid, unexpired, and bound to
    ``messenger_id``. Constant-time on the signature; plain compare on the id
    (the id is not secret — it's in the deep link)."""
    payload = open_sealed(token, _secret(settings))
    if payload is None:
        return False
    if payload.get("mid") != messenger_id:
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return False
    from datetime import UTC, datetime

    return datetime.now(UTC).timestamp() <= exp


def decode_connect_token(token: str, *, settings) -> dict[str, Any] | None:
    """Decode without the messenger-id check (used by /start to learn which
    messenger the user is trying to connect). Still verifies signature + TTL."""
    payload = open_sealed(token, _secret(settings))
    if payload is None:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    from datetime import UTC, datetime

    if datetime.now(UTC).timestamp() > exp:
        return None
    return payload


__all__ = ["decode_connect_token", "issue_connect_token", "verify_connect_token"]