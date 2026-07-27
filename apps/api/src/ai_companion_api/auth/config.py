"""Deployment mode + auth-backend enums and the boot-time validity matrix.

The matrix encodes the "for local, only local accounts" rule symmetrically so the
SaaS never runs a self-managed password store::

    self_hosted + local profile  → local only
    self_hosted + sso   profile  → oidc / trusted_header / magic_link
    hosted                       → oidc / magic_link  (local forbidden)

``bootstrap.validate_auth_config`` enforces it at startup (fail fast, no silent
fallback) and is unit-tested in ``tests/test_auth_bootstrap.py``.
"""

from __future__ import annotations

from enum import StrEnum


class DeploymentMode(StrEnum):
    self_hosted = "self_hosted"
    hosted = "hosted"


class SelfHostedProfile(StrEnum):
    local = "local"
    sso = "sso"


class AuthBackendKind(StrEnum):
    local = "local"
    oidc = "oidc"
    magic_link = "magic_link"
    trusted_header = "trusted_header"


# (mode, profile) → set of allowed auth backends. ``profile`` is None in hosted.
ALLOWED_BACKENDS: dict[tuple[str, str | None], frozenset[str]] = {
    (DeploymentMode.self_hosted.value, SelfHostedProfile.local.value): frozenset(
        {AuthBackendKind.local.value}
    ),
    (DeploymentMode.self_hosted.value, SelfHostedProfile.sso.value): frozenset(
        {
            AuthBackendKind.oidc.value,
            AuthBackendKind.trusted_header.value,
            AuthBackendKind.magic_link.value,
        }
    ),
    (DeploymentMode.hosted.value, None): frozenset(
        {AuthBackendKind.oidc.value, AuthBackendKind.magic_link.value}
    ),
}


def allowed_backends(mode: str, profile: str | None) -> frozenset[str]:
    """Return the set of auth backends the matrix permits for this mode/profile.

    Unknown mode/profile → empty set (the caller rejects).
    """
    key = (mode, profile if mode == DeploymentMode.self_hosted.value else None)
    return ALLOWED_BACKENDS.get(key, frozenset())


__all__ = [
    "ALLOWED_BACKENDS",
    "AuthBackendKind",
    "DeploymentMode",
    "SelfHostedProfile",
    "allowed_backends",
]
