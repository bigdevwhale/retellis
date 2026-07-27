"""Trusted-header backend — HMAC signature + internal-origin enforcement.

The header is only meaningful behind an identity-aware proxy. The HMAC is the
spoofing guard; the internal-origin check is defense in depth. Tests cover:
valid signed header from loopback → Principal; missing signature → 401;
tampered signature → 401; external origin (when required) → 401.
"""

from __future__ import annotations

import hashlib
import hmac


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


async def test_trusted_header_signs_in(make_app, app_client):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="trusted_header",
        AUTH_HEADER_HMAC_SECRET="topsecret",
        AUTH_TRUSTED_REQUIRE_INTERNAL="1",
    )
    async with app_client(app) as ac:  # default client is loopback → internal
        val = "alice@example.com"
        sig = _sign("topsecret", val)
        r = await ac.get(
            "/v1/providers",
            headers={"X-Remote-User": val, "X-Remote-User-Sig": sig},
        )
        assert r.status_code == 200
        me = await ac.get("/v1/auth/me", headers={"X-Remote-User": val, "X-Remote-User-Sig": sig})
        assert me.json()["subject"] == val
        assert me.json()["auth_backend"] == "trusted_header"


async def test_trusted_header_rejects_missing_signature(make_app, app_client):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="trusted_header",
        AUTH_HEADER_HMAC_SECRET="topsecret",
    )
    async with app_client(app) as ac:
        r = await ac.get("/v1/providers", headers={"X-Remote-User": "alice@example.com"})
        assert r.status_code == 401


async def test_trusted_header_rejects_tampered_signature(make_app, app_client):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="trusted_header",
        AUTH_HEADER_HMAC_SECRET="topsecret",
    )
    async with app_client(app) as ac:
        r = await ac.get(
            "/v1/providers",
            headers={"X-Remote-User": "alice@example.com", "X-Remote-User-Sig": "deadbeef"},
        )
        assert r.status_code == 401


async def test_trusted_header_rejects_external_origin(make_app, app_client):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="trusted_header",
        AUTH_HEADER_HMAC_SECRET="topsecret",
        AUTH_TRUSTED_REQUIRE_INTERNAL="1",
    )
    # A public peer address → the internal-origin check rejects even a
    # correctly signed header. (203.0.113.x is TEST-NET-3, which Python's
    # ipaddress treats as is_private — use a real public address instead.)
    async with app_client(app, client=("8.8.8.8", 123)) as ac:
        val = "alice@example.com"
        sig = _sign("topsecret", val)
        r = await ac.get(
            "/v1/providers",
            headers={"X-Remote-User": val, "X-Remote-User-Sig": sig},
        )
        assert r.status_code == 401
