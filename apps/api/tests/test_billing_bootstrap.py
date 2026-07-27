"""Billing bootstrap validation — hosted + feature_billing requires a provider.

``validate_auth_config`` runs in ``create_app`` (main.py:151), so a misconfigured
hosted deployment that turns on billing without any provider secret refuses to
boot (``AuthConfigError``) rather than advertise a checkout it can't serve
("disclose, don't perform"). At least one of PADDLE_API_KEY (WW) or
YUKASSA_SHOP_ID (RU) must be set.
"""

from __future__ import annotations

import pytest

from ai_companion_api.auth.bootstrap import AuthConfigError

# Shared hosted env that boots cleanly EXCEPT for the billing-provider check.
# magic_link is the zero-external-dep hosted backend (console email transport).
_HOSTED_OK = dict(
    DEPLOYMENT_MODE="hosted",
    AUTH_BACKEND="magic_link",
    AUTH_MAGIC_LINK_SECRET="ml-secret",
    AUTH_EMAIL_TRANSPORT="console",
    PUBLIC_ORIGIN="https://app.example.com",
    FEATURE_BILLING="1",
    PADDLE_API_KEY="paddle_live_test_secret_value_0123456789",
)


async def test_hosted_billing_without_provider_fails_to_boot(make_app):
    # Billing on but NEITHER provider configured → boot must refuse.
    env = {k: v for k, v in _HOSTED_OK.items() if k != "PADDLE_API_KEY"}
    with pytest.raises(AuthConfigError, match="billing provider"):
        make_app(**env)


async def test_hosted_billing_with_paddle_boots(make_app):
    # Paddle configured (WW) → boots.
    app = make_app(**_HOSTED_OK)
    assert app is not None


async def test_hosted_billing_with_yukassa_boots(make_app):
    # ЮKassa configured (RU) without Paddle → also boots (single-geo deploy).
    env = {k: v for k, v in _HOSTED_OK.items() if k != "PADDLE_API_KEY"}
    env["YUKASSA_SHOP_ID"] = "123456"
    env["YUKASSA_SECRET_KEY"] = "yukassa_live_test_secret_value_0123456789"
    app = make_app(**env)
    assert app is not None


async def test_self_hosted_billing_never_requires_provider(make_app):
    # feature_billing is gated `and is_hosted` — on self_hosted the flag is
    # irrelevant and the provider check must NOT fire (no boot failure).
    app = make_app(
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="local",
        AUTH_BACKEND="local",
        FEATURE_BILLING="1",
    )
    assert app is not None
