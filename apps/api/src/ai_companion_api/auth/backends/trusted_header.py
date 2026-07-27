"""Trusted-header backend — for self-hosted owners who run an identity-aware proxy.

The front proxy (OAuth2 Proxy / Authelia / Traefik Forward Auth / Caddy / nginx
auth_request) authenticates the user against any IdP (OIDC / SAML / LDAP / …) and
sets a header FastAPI trusts. Spoofing is prevented by an HMAC-SHA256 signature
over the header value (shared secret ``AUTH_HEADER_HMAC_SECRET``); optionally also
requiring the request to originate from a private/loopback address.

Invariant: **never expose the API directly when this backend is on** — the header
is only meaningful behind the proxy. The HMAC is the real spoofing guard; the
internal-origin check is defense in depth. ``bootstrap`` refuses to boot this
backend without the HMAC secret.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress

from ai_companion_contracts import Principal

from ...config import Settings
from ..principal import principal_from_user
from ..store import AuthStore, UserRecord


def _is_internal_host(host: str | None) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


class TrustedHeaderBackend:
    name = "trusted_header"

    def __init__(self, settings: Settings, store: AuthStore) -> None:
        self.settings = settings
        self.store = store

    async def resolve(self, request) -> Principal | None:  # noqa: ANN001
        s = self.settings
        value = request.headers.get(s.auth_trusted_header_name)
        if not value:
            return None
        sig = request.headers.get(s.auth_trusted_header_sig_name, "")
        expected = _sign(s.auth_header_hmac_secret, value)
        if not hmac.compare_digest(sig, expected):
            return None
        if s.auth_trusted_require_internal:
            client_host = request.client.host if request.client else None
            if not _is_internal_host(client_host):
                return None
        user = await self._get_or_create_user(value)
        return principal_from_user(user, self.name)

    async def _get_or_create_user(self, value: str) -> UserRecord:
        existing = await self.store.get_user_by_subject(issuer="trusted-header", subject=value)
        if existing is not None:
            return existing
        # Optional email header the proxy may set.
        return await self.store.create_user(
            issuer="trusted-header",
            subject=value,
            email=None,
            display_name=value,
            password_hash=None,
            plan="self_hosted_free",
            credits_usd=0.0,
        )


__all__ = ["TrustedHeaderBackend"]
