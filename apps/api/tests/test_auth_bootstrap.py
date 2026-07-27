"""Boot-time mode→backend matrix validation.

Enforces the "for local, only local accounts" rule symmetrically: self-hosted
local profile ⇒ local only; self-hosted sso ⇒ oidc/trusted_header/magic_link;
hosted ⇒ oidc/magic_link (local forbidden). Plus per-backend prerequisites.
"""

from __future__ import annotations

import pytest

from ai_companion_api.auth.bootstrap import AuthConfigError, validate_auth_config
from ai_companion_api.config import Settings


def _settings(monkeypatch, **env) -> Settings:
    # conftest globally sets AUTH_ALLOW_INSECURE_USER_HEADER=1 for the legacy
    # ``client`` fixture. These bootstrap tests construct Settings() directly
    # and are about the mode→backend matrix, not the escape hatch — so default
    # it OFF here to isolate them (and so I17's hosted+insecure=1 boot-fail
    # doesn't fire on every hosted matrix case). Callers that want to exercise
    # I17 pass AUTH_ALLOW_INSECURE_USER_HEADER=1 explicitly.
    env.setdefault("AUTH_ALLOW_INSECURE_USER_HEADER", "0")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    return Settings()


def test_default_config_is_self_hosted_local(monkeypatch):
    s = _settings(monkeypatch)
    mode, profile, backend = validate_auth_config(s)
    assert mode == "self_hosted"
    assert profile == "local"
    assert backend == "local"


def test_self_hosted_local_rejects_oidc(monkeypatch):
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="local",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="x",
    )
    with pytest.raises(AuthConfigError, match="not allowed"):
        validate_auth_config(s)


def test_self_hosted_local_rejects_magic_link(monkeypatch):
    s = _settings(monkeypatch, AUTH_BACKEND="magic_link", AUTH_MAGIC_LINK_SECRET="x")
    with pytest.raises(AuthConfigError):
        validate_auth_config(s)


def test_self_hosted_sso_allows_oidc(monkeypatch):
    s = _settings(
        monkeypatch,
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="x",
    )
    assert validate_auth_config(s) == ("self_hosted", "sso", "oidc")


def test_self_hosted_sso_rejects_local(monkeypatch):
    s = _settings(monkeypatch, AUTH_SELF_HOSTED_PROFILE="sso", AUTH_BACKEND="local")
    with pytest.raises(AuthConfigError, match="not allowed"):
        validate_auth_config(s)


def test_hosted_rejects_local(monkeypatch):
    s = _settings(monkeypatch, DEPLOYMENT_MODE="hosted", AUTH_BACKEND="local")
    with pytest.raises(AuthConfigError, match="not allowed"):
        validate_auth_config(s)


def test_hosted_allows_oidc(monkeypatch):
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="x",
        PUBLIC_ORIGIN="https://app.example.com",
    )
    assert validate_auth_config(s) == ("hosted", None, "oidc")


def test_hosted_rejects_insecure_user_header(monkeypatch):
    """I17: the X-User-Id escape hatch is full impersonation in hosted
    multi-user mode — boot must refuse rather than serve under a spoofable
    identity. Self-hosted may still enable it for dev/test."""
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="x",
        AUTH_ALLOW_INSECURE_USER_HEADER="1",
        PUBLIC_ORIGIN="https://app.example.com",
    )
    with pytest.raises(AuthConfigError, match="INSECURE_USER_HEADER"):
        validate_auth_config(s)


def test_hosted_rejects_http_origin(monkeypatch):
    """A hosted deployment on an http origin would hand out a non-Secure
    14-day session cookie (``cookie_secure`` returns False for http). Refuse
    to boot unless PUBLIC_ORIGIN is https."""
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="x",
        PUBLIC_ORIGIN="http://app.example.com",
    )
    with pytest.raises(AuthConfigError, match="PUBLIC_ORIGIN"):
        validate_auth_config(s)


def test_self_hosted_allows_http_origin(monkeypatch):
    """Self-hosted may run on http://localhost — the local network is the
    operator's own threat model, so http is permitted there."""
    s = _settings(monkeypatch, PUBLIC_ORIGIN="http://localhost:3000")
    assert validate_auth_config(s) == ("self_hosted", "local", "local")


def test_self_hosted_allows_insecure_user_header(monkeypatch):
    """I17 control: self-hosted may keep the dev/test escape hatch on."""
    s = _settings(
        monkeypatch,
        DEPLOYMENT_MODE="self_hosted",
        AUTH_ALLOW_INSECURE_USER_HEADER="1",
    )
    assert validate_auth_config(s) == ("self_hosted", "local", "local")


def test_oidc_requires_issuer_and_client_id(monkeypatch):
    s = _settings(
        monkeypatch, AUTH_SELF_HOSTED_PROFILE="sso", AUTH_BACKEND="oidc", AUTH_STATE_SECRET="x"
    )
    with pytest.raises(AuthConfigError, match="OIDC_ISSUER"):
        validate_auth_config(s)


def test_oidc_requires_state_secret(monkeypatch):
    s = _settings(
        monkeypatch,
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp",
        OIDC_CLIENT_ID="c",
    )
    with pytest.raises(AuthConfigError, match="AUTH_STATE_SECRET"):
        validate_auth_config(s)


def test_trusted_header_requires_hmac_secret(monkeypatch):
    s = _settings(monkeypatch, AUTH_SELF_HOSTED_PROFILE="sso", AUTH_BACKEND="trusted_header")
    with pytest.raises(AuthConfigError, match="AUTH_HEADER_HMAC_SECRET"):
        validate_auth_config(s)


def test_magic_link_requires_secret_and_transport(monkeypatch):
    s = _settings(
        monkeypatch,
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="magic_link",
        AUTH_EMAIL_TRANSPORT="off",
    )
    with pytest.raises(AuthConfigError, match="AUTH_MAGIC_LINK_SECRET"):
        validate_auth_config(s)
    s2 = _settings(
        monkeypatch,
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="x",
        AUTH_EMAIL_TRANSPORT="off",
    )
    with pytest.raises(AuthConfigError, match="AUTH_EMAIL_TRANSPORT"):
        validate_auth_config(s2)


def test_invalid_mode_rejected(monkeypatch):
    s = _settings(monkeypatch, DEPLOYMENT_MODE="cloud")
    with pytest.raises(AuthConfigError, match="DEPLOYMENT_MODE"):
        validate_auth_config(s)
