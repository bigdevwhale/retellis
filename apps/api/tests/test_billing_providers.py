"""Paddle outbound provider calls: real hosted-checkout + customer-portal shape.

These exercise the REAL ``POST /transactions`` (hosted checkout) and
``POST /customers/{id}/portal-sessions`` (customer portal) integration logic
with the outbound HTTP call injected (``poster``) — so the URL we build, the
body we send, and the response field we extract (``data.checkout.url`` /
``data.urls.general.overview``) are asserted against the real Paddle Billing
API shape, not a fictional one. The 503 path (no API key / no price link / no
customer id) is the honest "checkout unavailable" surface.
"""

from __future__ import annotations

import pytest

from ai_companion_api.billing.providers import (
    _ProviderUnavailable,
    paddle_create_checkout,
    paddle_create_portal,
)
from ai_companion_api.billing.store import SEED_PLANS, InMemoryBillingStore
from ai_companion_api.config import Settings

_SETTINGS_KW = dict(
    paddle_api_key="paddle_live_test_secret_value_0123456789", paddle_environment="sandbox"
)


def _plan(*, price_id: str | None = "pri_test_plus_ww"):
    return SEED_PLANS[0].model_copy(update={"provider_price_id": price_id})


# --- hosted checkout: POST /transactions → data.checkout.url ---


async def test_checkout_extracts_checkout_url_and_txn_id():
    settings = Settings(**_SETTINGS_KW)
    captured: dict[str, object] = {}

    async def poster(url, body, s):
        captured["url"] = url
        captured["body"] = body
        return {
            "checkout": {"url": "https://sandbox-checkout.paddle.com/pay?_ptxn=txn_1"},
            "id": "txn_1",
        }

    out = await paddle_create_checkout(
        plan=_plan(),
        user_id="u-1",
        return_url="https://app.example.com/plans?checkout=done",
        settings=settings,
        poster=poster,
    )
    # We POST to /transactions (NOT /transactions/preview — that was the stub).
    assert captured["url"] == "https://sandbox-api.paddle.com/transactions"
    assert captured["body"]["items"] == [{"price_id": "pri_test_plus_ww", "quantity": 1}]
    # passthrough so the webhook links the payment back without trusting the redirect
    assert captured["body"]["custom_data"] == {"user_id": "u-1", "plan_slug": "plus_ww"}
    # No return_url in the transactions body — Paddle has no such field; the
    # after-payment redirect is configured on the default payment link.
    assert "return_url" not in captured["body"]
    assert out.redirect_url == "https://sandbox-checkout.paddle.com/pay?_ptxn=txn_1"
    assert out.provider_sub_id == "txn_1"  # the checkout transaction id


async def test_checkout_falls_back_to_return_url_when_no_checkout_url():
    settings = Settings(**_SETTINGS_KW)

    async def poster(url, body, s):
        return {"id": "txn_2"}  # no checkout.url (e.g. collection_mode: manual)

    out = await paddle_create_checkout(
        plan=_plan(),
        user_id="u-1",
        return_url="https://app.example.com/plans?checkout=done",
        settings=settings,
        poster=poster,
    )
    assert out.redirect_url == "https://app.example.com/plans?checkout=done"


async def test_checkout_503_without_api_key():
    settings = Settings(paddle_api_key="", paddle_environment="sandbox")
    with pytest.raises(_ProviderUnavailable):
        await paddle_create_checkout(
            plan=_plan(), user_id="u-1", return_url="https://app.example.com", settings=settings
        )


async def test_checkout_503_when_plan_not_linked():
    # provider_price_id is NULL until the operator links the price in the Paddle
    # dashboard — honest 503, the plan isn't buyable yet.
    settings = Settings(**_SETTINGS_KW)
    with pytest.raises(_ProviderUnavailable):
        await paddle_create_checkout(
            plan=_plan(price_id=None),
            user_id="u-1",
            return_url="https://app.example.com",
            settings=settings,
        )


# --- customer portal: POST /customers/{id}/portal-sessions ---


async def _store_with_customer(user_id: str = "u-1", customer_id: str = "ctm_1"):
    store = InMemoryBillingStore()
    await store.upsert_billing_profile(
        user_id=user_id, country="WW", provider="paddle", provider_customer_id=customer_id
    )
    return store


async def test_portal_extracts_overview_url():
    settings = Settings(**_SETTINGS_KW)
    store = await _store_with_customer()
    captured: dict[str, object] = {}

    async def poster(url, s):
        captured["url"] = url
        return {"urls": {"general": {"overview": "https://sandbox-customer-portal.paddle.com/..."}}}

    out = await paddle_create_portal(
        user_id="u-1", settings=settings, billing_store=store, poster=poster
    )
    assert captured["url"] == "https://sandbox-api.paddle.com/customers/ctm_1/portal-sessions"
    assert out.redirect_url == "https://sandbox-customer-portal.paddle.com/..."


async def test_portal_503_without_api_key():
    settings = Settings(paddle_api_key="", paddle_environment="sandbox")
    store = await _store_with_customer()
    with pytest.raises(_ProviderUnavailable):
        await paddle_create_portal(user_id="u-1", settings=settings, billing_store=store)


async def test_portal_503_without_customer_id():
    # No billing profile (the user hasn't paid) → nothing to manage → 503.
    settings = Settings(**_SETTINGS_KW)
    store = InMemoryBillingStore()
    with pytest.raises(_ProviderUnavailable):
        await paddle_create_portal(user_id="u-1", settings=settings, billing_store=store)


async def test_portal_503_when_profile_has_no_customer_id():
    # Profile exists (checkout ran) but no customer_id persisted yet (webhook
    # hasn't landed) → 503 rather than a broken portal link.
    settings = Settings(**_SETTINGS_KW)
    store = InMemoryBillingStore()
    await store.upsert_billing_profile(user_id="u-1", country="WW", provider="paddle")
    with pytest.raises(_ProviderUnavailable):
        await paddle_create_portal(user_id="u-1", settings=settings, billing_store=store)


async def test_portal_503_when_response_has_no_overview():
    settings = Settings(**_SETTINGS_KW)
    store = await _store_with_customer()

    async def poster(url, s):
        return {"urls": {"general": {}}}  # malformed / unexpected shape

    with pytest.raises(_ProviderUnavailable):
        await paddle_create_portal(
            user_id="u-1", settings=settings, billing_store=store, poster=poster
        )
