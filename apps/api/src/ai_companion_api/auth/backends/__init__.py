"""Auth backends — swappable identity providers selected at boot."""

from __future__ import annotations

from ...config import Settings
from ..config import AuthBackendKind
from ..store import AuthStore
from .base import AuthBackend, AuthError
from .local import LocalAccountsBackend
from .magic_link import MagicLinkBackend
from .oidc import OIDCBackend
from .trusted_header import TrustedHeaderBackend


def build_backend(settings: Settings, store: AuthStore) -> AuthBackend:
    """Construct the active auth backend from validated settings.

    The mode→backend matrix has already been validated by
    ``bootstrap.validate_auth_config`` at boot, so we trust ``auth_backend`` here.
    HTTP / email transports are injectable for tests (see each backend)."""
    kind = settings.auth_backend
    if kind == AuthBackendKind.local.value:
        return LocalAccountsBackend(settings, store)
    if kind == AuthBackendKind.trusted_header.value:
        return TrustedHeaderBackend(settings, store)
    if kind == AuthBackendKind.oidc.value:
        return OIDCBackend(settings, store)
    if kind == AuthBackendKind.magic_link.value:
        return MagicLinkBackend(settings, store)
    raise AuthError(500, f"unknown auth backend: {kind}")


__all__ = [
    "AuthBackend",
    "AuthError",
    "LocalAccountsBackend",
    "MagicLinkBackend",
    "OIDCBackend",
    "TrustedHeaderBackend",
    "build_backend",
]
