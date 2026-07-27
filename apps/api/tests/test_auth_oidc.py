"""OIDC backend end-to-end against a stub IdP (no network).

Injects a stub HTTP client into the OIDC backend so discovery / token exchange /
userinfo are driven by fixture data. Covers the PKCE state cookie round-trip:
begin → callback (state verified) → session cookie → /me.
"""

from __future__ import annotations

import urllib.parse

import pytest

from ai_companion_api.auth.backends.oidc import OIDCBackend

DISCOVERY = {
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",
    "userinfo_endpoint": "https://idp.example/userinfo",
}
TOKENS = {"access_token": "abc123", "id_token": "ignored"}
USERINFO = {"sub": "oidc-42", "email": "carol@example.com", "name": "Carol"}


class _Resp:
    def __init__(self, data, status=200):
        self._d = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._d


class _StubClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if "openid-configuration" in url:
            return _Resp(DISCOVERY)
        if "userinfo" in url:
            return _Resp(USERINFO)
        return _Resp({}, status=404)

    async def post(self, url, data=None):
        assert "token" in url
        return _Resp(TOKENS)


def _factory():
    return _StubClient()


async def test_oidc_begin_callback_me(make_app, app_client):
    app = make_app(
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="client-1",
        AUTH_STATE_SECRET="state-secret",
    )
    async with app_client(app) as ac:
        # Swap in the stub HTTP client (the real one would hit the network).
        app.state.auth_backend = OIDCBackend(
            app.state.settings, app.state.auth_store, http_client_factory=_factory
        )

        # 1) begin → 303 redirect to the authorize URL + a state cookie.
        begin = await ac.get("/v1/auth/begin")
        assert begin.status_code == 303
        loc = begin.headers["location"]
        assert "https://idp.example/authorize" in loc
        qs = dict(urllib.parse.parse_qs(urllib.parse.urlparse(loc).query))
        state = qs["state"][0]
        assert qs["code_challenge_method"][0] == "S256"
        assert "stillside_oidc_state" in ac.cookies

        # 2) callback with the matching state + a fake code → session cookie.
        cb = await ac.get("/v1/auth/callback", params={"code": "fake-code", "state": state})
        assert cb.status_code == 303
        assert (
            cb.headers["location"].rstrip("/").endswith("://localhost:3000")
            or "/v1" not in cb.headers["location"]
        )
        assert "stillside_sess" in ac.cookies

        # 3) /me returns the OIDC principal.
        me = await ac.get("/v1/auth/me")
        assert me.status_code == 200
        p = me.json()
        assert p["subject"] == "oidc-42"
        assert p["email"] == "carol@example.com"
        assert p["issuer"] == "https://idp.example"
        assert p["auth_backend"] == "oidc"


async def test_oidc_callback_rejects_state_mismatch(make_app, app_client):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="client-1",
        AUTH_STATE_SECRET="state-secret",
    )
    async with app_client(app) as ac:
        app.state.auth_backend = OIDCBackend(
            app.state.settings, app.state.auth_store, http_client_factory=_factory
        )
        await ac.get("/v1/auth/begin")  # sets the state cookie
        bad = await ac.get("/v1/auth/callback", params={"code": "x", "state": "wrong-state"})
        assert bad.status_code == 400
        assert "state" in bad.json()["detail"].lower()


@pytest.mark.parametrize("missing", ["code", "state"])
async def test_oidc_callback_missing_params_redirects_home(make_app, app_client, missing):
    app = make_app(
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="client-1",
        AUTH_STATE_SECRET="state-secret",
    )
    async with app_client(app) as ac:
        app.state.auth_backend = OIDCBackend(
            app.state.settings, app.state.auth_store, http_client_factory=_factory
        )
        params = {"code": "x", "state": "y"}
        params.pop(missing)
        r = await ac.get("/v1/auth/callback", params=params)
        assert r.status_code == 303  # sent home unauthenticated, no session set
        assert "stillside_sess" not in ac.cookies
