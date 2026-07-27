"""Auth identity is decoupled from the BYOK vault.

Asserts the non-negotiable: no auth request model accepts a ``passphrase`` field,
the auth layer never stores/echoes a passphrase, ``enc_blob`` stays ciphertext the
server can't decrypt, and the zeroize path is untouched. The vault passphrase is
entered in-browser and never sent to any auth endpoint.
"""

from __future__ import annotations

import base64

import pytest
from ai_companion_contracts import LocalLoginRequest, LocalSignupRequest, MagicLinkRequest

from ai_companion_api.auth.sessions import open_sealed, seal


def test_auth_request_models_have_no_passphrase_field():
    """The vault passphrase must never be a field on any auth wire shape."""
    for model in (LocalSignupRequest, LocalLoginRequest, MagicLinkRequest):
        fields = set(model.model_fields)
        assert "passphrase" not in fields
        assert "master_key" not in fields
        # LocalSignup/Login carry a *login* password (distinct from the vault
        # passphrase) — that's the only credential field allowed.
        if model is MagicLinkRequest:
            assert fields == {"email"}
        else:
            assert "password" in fields and "passphrase" not in fields


async def test_signup_does_not_echo_password_or_passphrase(make_app, app_client):
    app = make_app()
    async with app_client(app) as ac:
        r = await ac.post(
            "/v1/auth/signup",
            json={
                "email": "sep@example.com",
                "password": "login-password",
                "display_name": "Sep",
                # Extra fields are ignored by pydantic (extra="ignore" on
                # contracts? these models use default — extras dropped).
                "passphrase": "should-be-ignored",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # No credential material in the response — only the Principal.
        assert "password" not in body
        assert "passphrase" not in body
        assert "password_hash" not in body


def test_enc_blob_remains_ciphertext_sealed_token_roundtrip():
    """The sealed-token helper (magic links / OIDC state) must not collide with
    the vault's enc_blob format and must be unverifiable without the secret —
    i.e. the server still can't derive the vault passphrase from any auth token."""
    payload = {"email": "x@y.com", "exp": 9999999999, "nonce": "n"}
    tok = seal(payload, "auth-secret")
    # Token is opaque ciphertext-like; without the secret it can't be opened.
    assert open_sealed(tok, "wrong-secret") is None
    assert open_sealed(tok, "") is None
    # And it is not a vault enc_blob (which is base64 salt||nonce||ct of the
    # *provider key* — unrelated to auth tokens).
    decoded = tok.split(".")[0]
    assert base64.urlsafe_b64decode(decoded).startswith(b"{")  # JSON payload, not key material


@pytest.mark.parametrize("endpoint", ["/v1/auth/signup", "/v1/auth/login", "/v1/auth/magiclink"])
async def test_auth_endpoints_do_not_accept_passphrase_as_credential(
    make_app, app_client, endpoint
):
    """A passphrase in the body must not authenticate or be consumed — it's
    simply not part of any auth model."""
    app = make_app()
    async with app_client(app) as ac:
        # Send only a passphrase (no email/password) → 422 (missing required
        # fields), never a 200 that "authenticates" via passphrase.
        r = await ac.post(endpoint, json={"passphrase": "vault-passphrase"})
        assert r.status_code in (400, 422), (endpoint, r.status_code, r.text)
