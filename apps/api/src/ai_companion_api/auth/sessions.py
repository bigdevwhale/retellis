"""Session cookie + sealed-token helpers.

The session cookie carries an opaque session token (a row in ``sessions``) — never
the master key, a provider key, or the vault passphrase. It is HttpOnly + Secure +
SameSite=Lax. ``Secure`` follows the public origin scheme so localhost dev over
HTTP still receives the cookie; in production (HTTPS via Caddy) it is locked on.

The sealed-token helpers (HMAC-SHA256) are used for OIDC PKCE state cookies and
magic-link tokens — short-lived, signed, tamper-evident payloads the server can
verify without keeping server-side state (multi-process safe).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import Response

from ..config import Settings


def cookie_secure(settings: Settings) -> bool:
    """Secure flag = True unless the public origin is plain HTTP (localhost dev)."""
    return settings.public_origin.startswith("https://")


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.auth_session_cookie,
        value=token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=cookie_secure(settings),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_session_cookie,
        path="/",
        secure=cookie_secure(settings),
        samesite="lax",
        httponly=True,
    )


def seal(payload: dict[str, Any], secret: str) -> str:
    """Sign a JSON payload with HMAC-SHA256 → ``base64(payload).hex_mac``."""
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{body.decode('ascii')}.{mac}"


def open_sealed(token: str, secret: str) -> dict[str, Any] | None:
    """Verify and decode a sealed token. Returns None on tamper / bad shape."""
    if not secret or "." not in token:
        return None
    body_b64, mac = token.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body_b64.encode("ascii")).decode("utf-8"))
    except Exception:  # noqa: BLE001 — any decode failure is just "invalid token"
        return None


__all__ = ["clear_session_cookie", "cookie_secure", "open_sealed", "seal", "set_session_cookie"]
