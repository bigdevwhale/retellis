"""AuthBackend protocol + shared error type.

Backends produce a verified ``Principal`` (trusted-header) or establish a session
(local / oidc / magic-link) that the middleware later resolves to a Principal. The
router branches on ``backend.name`` to call the login-flow methods a backend
actually implements; the ``AuthBackend`` protocol keeps the common surface small.
"""

from __future__ import annotations

from typing import Protocol

from ai_companion_contracts import Principal

from ...config import Settings
from ..store import AuthStore


class AuthError(Exception):
    """Raised for client-facing auth failures (bad credentials, conflict, …).

    ``status_code`` maps to the HTTP response; ``detail`` is safe to surface (it
    never carries key material or a password)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class AuthBackend(Protocol):
    name: str
    settings: Settings
    store: AuthStore

    async def resolve(self, request) -> Principal | None: ...  # type: ignore[no-untyped-def]


__all__ = ["AuthBackend", "AuthError"]
