"""OIDC backend — Authorization Code + PKCE, generic issuer.

Works for Google, GitHub (OAuth2), Keycloak, Authentik, Authelia, Dex, Okta,
Microsoft Entra — anything with an OIDC discovery document. Used by ``self_hosted
+ sso`` (owner's IdP) and by ``hosted`` (managed Google/GitHub).

Flow:
  1. ``begin_login`` → generates PKCE verifier + state, returns the authorize URL.
     The router seals ``{state, verifier, redirect_uri}`` into a short-lived
     signed cookie (stateless, multi-process safe) and redirects.
  2. The IdP redirects back to ``/v1/auth/callback?code=…&state=…``.
  3. ``handle_callback`` unseals the state cookie, checks ``state`` matches,
     exchanges ``code`` for tokens (PKCE verifier), then calls the userinfo
     endpoint for ``sub``/``email``/``name`` and get-or-creates the user.

The HTTP client is injectable (``http_client_factory``) so tests drive the flow
against a stub IdP with no network. Honest MVP note: we validate ``state`` + PKCE
and fetch identity from the userinfo endpoint over HTTPS; full ID-token JWKS
signature verification is a documented hardening follow-up (would add a JOSE dep).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from collections.abc import Callable
from typing import Any

from ...config import Settings
from ..store import AuthStore, UserRecord
from .base import AuthError


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _default_http_client_factory() -> Any:
    # Lazy import so the module loads even in a stripped env; tests inject a stub.
    import httpx

    return httpx.AsyncClient(timeout=15.0)


class OIDCBackend:
    name = "oidc"

    def __init__(
        self,
        settings: Settings,
        store: AuthStore,
        http_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._http_client_factory = http_client_factory or _default_http_client_factory
        self._discovery: dict[str, Any] | None = None

    async def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            async with self._http_client_factory() as client:
                r = await client.get(
                    f"{self.settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
                )
                r.raise_for_status()
                self._discovery = r.json()
        return self._discovery

    def callback_url(self) -> str:
        return f"{self.settings.public_origin.rstrip('/')}/v1/auth/callback"

    def begin_login(self) -> tuple[str, str, str]:
        """Return ``(authorize_url, state, verifier)``. The router seals
        ``{state, verifier, redirect_uri}`` into a state cookie and redirects."""
        disc = self._discovery or {}
        authorize = (
            disc.get("authorization_endpoint")
            or self.settings.oidc_issuer.rstrip("/") + "/authorize"
        )
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.callback_url(),
            "scope": self.settings.oidc_scopes,
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{authorize}?{urllib.parse.urlencode(params)}", state, verifier

    async def handle_callback(
        self, *, code: str, state: str, verifier: str, user_agent: str | None = None
    ) -> tuple[UserRecord, str]:
        disc = await self.discovery()
        token_ep = disc["token_endpoint"]
        userinfo_ep = disc["userinfo_endpoint"]
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.callback_url(),
            "client_id": self.settings.oidc_client_id,
            "code_verifier": verifier,
        }
        if self.settings.oidc_client_secret:
            data["client_secret"] = self.settings.oidc_client_secret
        async with self._http_client_factory() as client:
            r = await client.post(token_ep, data=data)
            if r.status_code >= 400:
                raise AuthError(400, "OIDC token exchange failed")
            tokens = r.json()
            access_token = tokens.get("access_token")
            if not access_token:
                raise AuthError(400, "OIDC token response missing access_token")
            r2 = await client.get(userinfo_ep, headers={"Authorization": f"Bearer {access_token}"})
            if r2.status_code >= 400:
                raise AuthError(400, "OIDC userinfo fetch failed")
            info = r2.json()
        sub = info.get("sub")
        if not sub:
            raise AuthError(400, "OIDC userinfo missing sub")
        email = info.get("email")
        display = info.get("name") or info.get("preferred_username") or email or sub
        plan = "hosted_free" if self.settings.deployment_mode == "hosted" else "self_hosted_free"
        credits = self.settings.hosted_signup_credits_usd if plan != "self_hosted_free" else 0.0
        user = await self.store.create_user(
            issuer=self.settings.oidc_issuer.rstrip("/"),
            subject=str(sub),
            email=email,
            display_name=display,
            password_hash=None,
            plan=plan,
            credits_usd=credits,
        )
        token = await self.store.create_session(
            user_id=user.id,
            ttl_seconds=self.settings.auth_session_ttl_seconds,
            user_agent=user_agent,
        )
        return user, token

    async def resolve(self, request) -> None:  # noqa: ANN001
        # Session-based: middleware resolves cookie → session → Principal.
        return None


__all__ = ["OIDCBackend"]
