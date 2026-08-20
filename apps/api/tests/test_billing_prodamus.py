"""Prodamus billing: signature, checkout, webhook state machine, endpoint.

Prodamus is the WW (+RU) card provider for operators who can't use Paddle
(e.g. a RU-resident самозанятый — Paddle blocks RU sellers). The checkout is a
one-off signed ``POST`` to the merchant's payform URL; the webhook is a JSON
callback whose ``Sign`` header is an HMAC-SHA256 over the ``submit`` sub-object
— verifying that signature is the ONLY auth on the route (it's in
``_PUBLIC_POST``). A successful ``payment_status`` grants the plan + additive
credits; ``order_canceled``/``order_denied`` only cancel. ``user_id`` +
``plan_slug`` are recovered from the ``order_num`` we encoded at checkout.
"""

from __future__ import annotations

import json

import pytest
from ai_companion_contracts import BillingProvider

from ai_companion_api.auth.store import InMemoryAuthStore
from ai_companion_api.billing.providers import (
    _prodamus_sign,
    prodamus_create_checkout,
    prodamus_create_portal,
    prodamus_parse_event,
    prodamus_verify_signature,
)
from ai_companion_api.billing.store import SEED_PLANS, InMemoryBillingStore
from ai_companion_api.config import Settings
from ai_companion_api.routers.billing import _provider_for_country, apply_prodamus_event

_SECRET = "prodamus_live_test_secret_value_0123456789"

_SETTINGS_KW = dict(
    prodamus_secret_key=_SECRET,
    prodamus_payform_url="https://demo.payform.ru",
    prodamus_sys="retellis",
    billing_return_origin="https://app.example.com",
)


def _plan():
    # Prodamus doesn't use provider_price_id (the link is built from name+price),
    # so a NULL price_id must NOT 503 — unlike Paddle.
    return SEED_PLANS[0].model_copy(update={"provider_price_id": None})


def _submit(**over) -> dict:
    base = {
        "order_id": "pro_internal_1",
        "order_num": "retellis:u-1:plus_ww:deadbeef",
        "payment_status": "success",
        "sum": "12.00",
        "currency": "usd",
        "products": [{"name": "Retellis Plus (plus_ww)", "price": "12.00", "quantity": "1"}],
    }
    base.update(over)
    return base


# --- routing: WW→Prodamus when configured, else Paddle; RU→ЮKassa when configured ---


def test_routing_ww_prefers_prodamus_when_configured():
    s = Settings(**_SETTINGS_KW)
    assert _provider_for_country("WW", s) is BillingProvider.prodamus
    assert _provider_for_country("US", s) is BillingProvider.prodamus  # any non-RU


def test_routing_ww_falls_back_to_paddle_without_prodamus():
    s = Settings(paddle_api_key="paddle_live_test_secret_value_0123456789")
    assert _provider_for_country("WW", s) is BillingProvider.paddle


def test_routing_ru_prefers_yookassa_then_prodamus():
    s = Settings(yukassa_shop_id="123456", yukassa_secret_key="x", prodamus_secret_key=_SECRET)
    assert _provider_for_country("RU", s) is BillingProvider.yookassa
    s2 = Settings(prodamus_secret_key=_SECRET)
    assert (
        _provider_for_country("RU", s2) is BillingProvider.prodamus
    )  # no ЮKassa → Prodamus (RU cards/SBP)


# --- signature (the ONLY auth on the webhook) ---


def test_sign_and_verify_roundtrip():
    submit = _submit()
    sign = _prodamus_sign(submit, _SECRET)
    assert prodamus_verify_signature({"submit": submit}, sign, _SECRET) is True


def test_verify_rejects_wrong_secret():
    submit = _submit()
    sign = _prodamus_sign(submit, _SECRET)
    assert (
        prodamus_verify_signature({"submit": submit}, sign, "wrong-secret-xxxxxxxxxxxxxx") is False
    )


def test_verify_rejects_missing_submit():
    assert prodamus_verify_signature({}, "anything", _SECRET) is False


def test_verify_rejects_missing_sign_header():
    assert prodamus_verify_signature({"submit": _submit()}, "", _SECRET) is False


def test_verify_accepts_cyrillic_in_submit():
    # payment_status_description / payment_type may carry Cyrillic. Whether
    # Prodamus unicode-escapes or not, ONE of the two canonicalizations must
    # match — so a Cyrillic-bearing submit still verifies.
    submit = _submit(payment_type="Оплата картой, выпущенной в РФ")
    sign_ascii = _prodamus_sign(submit, _SECRET, ensure_ascii=True)
    sign_raw = _prodamus_sign(submit, _SECRET, ensure_ascii=False)
    # Either sign must verify (the verifier tries both).
    assert prodamus_verify_signature({"submit": submit}, sign_ascii, _SECRET) is True
    assert prodamus_verify_signature({"submit": submit}, sign_raw, _SECRET) is True


# --- parser: order_num carries the linkage ---


def test_parse_success_reads_linkage_from_order_num():
    ev = prodamus_parse_event(
        {
            "order_id": "pro_1",
            "order_num": "retellis:u-1:plus_ww:deadbeef",
            "payment_status": "success",
        }
    )
    assert ev is not None
    assert ev.event_id == "pro_1"  # Prodamus internal — idempotency key
    assert ev.payment_status == "success"
    assert ev.user_id == "u-1"
    assert ev.plan_slug == "plus_ww"


def test_parse_canceled_status():
    ev = prodamus_parse_event(
        {
            "order_id": "pro_2",
            "order_num": "retellis:u-1:plus_ww:abc",
            "payment_status": "order_canceled",
        }
    )
    assert ev.payment_status == "order_canceled"


def test_parse_malformed_order_num_yields_no_linkage():
    ev = prodamus_parse_event(
        {"order_id": "pro_3", "order_num": "something-else", "payment_status": "success"}
    )
    assert ev.user_id is None
    assert ev.plan_slug is None


# --- checkout: POST to payform, do=link, signed ---


async def test_checkout_builds_signed_link_and_extracts_url():
    settings = Settings(**_SETTINGS_KW)
    captured: dict[str, object] = {}

    async def poster(url, flat, s):
        captured["url"] = url
        captured["flat"] = flat
        return "https://demo.payform.ru/u8zDE/"

    out = await prodamus_create_checkout(
        plan=_plan(),
        user_id="u-1",
        return_url="https://app.example.com/plans?checkout=done",
        settings=settings,
        poster=poster,
    )
    assert captured["url"] == "https://demo.payform.ru"
    f = captured["flat"]
    assert f["do"] == "link"
    assert f["sys"] == "retellis"
    assert f["callbackType"] == "json"  # webhook comes as JSON with `submit`
    assert f["npd_income_type"] == "FROM_INDIVIDUAL"  # самозанятый default
    assert f["currency"] == "usd"
    assert f["products[0][name]"] == "Retellis Plus (plus_ww)"
    assert f["products[0][price]"] == "12.00"  # major units, not cents
    assert f["products[0][quantity]"] == "1"
    assert f["urlNotification"] == "https://app.example.com/v1/billing/webhook/prodamus"
    assert f["urlSuccess"] == "https://app.example.com/plans?checkout=done"
    # order_num encodes the linkage the webhook will parse back.
    assert f["order_id"].startswith("retellis:u-1:plus_ww:")
    # A signature is present and is a valid hex HMAC over the nested params.
    assert "signature" in f and len(f["signature"]) == 64
    assert out.redirect_url == "https://demo.payform.ru/u8zDE/"
    assert out.provider_sub_id == f["order_id"]


async def test_checkout_503_without_secret():
    settings = Settings(
        prodamus_secret_key="", prodamus_payform_url="https://demo.payform.ru", prodamus_sys="s"
    )
    from ai_companion_api.billing.providers import _ProviderUnavailable

    with pytest.raises(_ProviderUnavailable):
        await prodamus_create_checkout(
            plan=_plan(), user_id="u-1", return_url="https://app.example.com", settings=settings
        )


async def test_checkout_503_without_payform_url_or_sys():
    from ai_companion_api.billing.providers import _ProviderUnavailable

    s1 = Settings(prodamus_secret_key=_SECRET, prodamus_payform_url="", prodamus_sys="s")
    s2 = Settings(
        prodamus_secret_key=_SECRET, prodamus_payform_url="https://demo.payform.ru", prodamus_sys=""
    )
    for s in (s1, s2):
        with pytest.raises(_ProviderUnavailable):
            await prodamus_create_checkout(
                plan=_plan(), user_id="u-1", return_url="https://app.example.com", settings=s
            )


async def test_checkout_503_when_response_is_not_a_url():
    settings = Settings(**_SETTINGS_KW)

    async def poster(url, flat, s):
        return "ERROR: bad signature"  # Prodamus rejected the request

    from ai_companion_api.billing.providers import _ProviderUnavailable

    with pytest.raises(_ProviderUnavailable):
        await prodamus_create_checkout(
            plan=_plan(),
            user_id="u-1",
            return_url="https://app.example.com",
            settings=settings,
            poster=poster,
        )


async def test_checkout_null_provider_price_id_does_not_503():
    # Unlike Paddle, Prodamus builds the link from name+price — a NULL
    # provider_price_id (the seed default until the operator links nothing) is fine.
    settings = Settings(**_SETTINGS_KW)

    async def poster(url, flat, s):
        return "https://demo.payform.ru/ok/"

    out = await prodamus_create_checkout(
        plan=_plan(),
        user_id="u-1",
        return_url="https://app.example.com",
        settings=settings,
        poster=poster,
    )
    assert out.redirect_url.startswith("http")


async def test_portal_503():
    # One-off payments: nothing to manage → honest 503 (no portal we can stand behind).
    from ai_companion_api.billing.providers import _ProviderUnavailable

    settings = Settings(**_SETTINGS_KW)
    with pytest.raises(_ProviderUnavailable):
        await prodamus_create_portal(user_id="u-1", settings=settings)


# --- state machine ---


async def _make_stores(subject: str = "u-1"):
    auth = InMemoryAuthStore()
    billing = InMemoryBillingStore()
    user = await auth.create_user(
        issuer="local",
        subject=subject,
        email="u@x.com",
        display_name="U",
        password_hash=None,
        plan="hosted_free",
        credits_usd=0.0,
    )
    return auth, billing, user.id


def _success_event(uid: str, *, nonce: str = "deadbeef", plan_slug: str = "plus_ww") -> object:
    return prodamus_parse_event(
        {
            "order_id": f"pro_{nonce}",
            "order_num": f"retellis:{uid}:{plan_slug}:{nonce}",
            "payment_status": "success",
            "sum": "12.00",
            "currency": "usd",
        }
    )


async def test_success_grants_plan_credits_and_invoice():
    auth, billing, uid = await _make_stores()
    ev = _success_event(uid)
    sub = await apply_prodamus_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    assert sub.cancel_at_period_end is True  # one-off — no auto-renew
    user = await auth.get_user(uid)
    assert user.plan == "plus_ww"
    assert user.credits_usd == pytest.approx(10.0)  # plus_ww credits grant
    invs = await billing.list_invoices(user_id=uid)
    assert len(invs) == 1
    assert invs[0].provider == "prodamus"
    assert invs[0].amount_cents == 1200  # 12.00 → cents
    assert invs[0].currency == "USD"
    assert invs[0].fiscal_receipt_id is None  # fiscalized on Prodamus's side


async def test_renewal_is_additive_and_updates_same_row():
    auth, billing, uid = await _make_stores()
    await apply_prodamus_event(_success_event(uid, nonce="aaa1"), store=billing, auth_store=auth)
    first_sub = await billing.get_subscription(user_id=uid)
    # A second payment (new order_num, same user+plan) → +10 more, ONE sub row.
    await apply_prodamus_event(_success_event(uid, nonce="bbb2"), store=billing, auth_store=auth)
    user = await auth.get_user(uid)
    assert user.credits_usd == pytest.approx(20.0)  # additive, NOT a reset to 10
    second_sub = await billing.get_subscription(user_id=uid)
    assert second_sub.id == first_sub.id  # same row, updated provider_sub_id
    assert second_sub.provider_sub_id == f"retellis:{uid}:plus_ww:bbb2"
    # Two invoices (one per payment), one subscription.
    assert len(await billing.list_invoices(user_id=uid)) == 2


async def test_canceled_sets_canceled_no_grant():
    auth, billing, uid = await _make_stores()
    await apply_prodamus_event(_success_event(uid), store=billing, auth_store=auth)
    ev = prodamus_parse_event(
        {
            "order_id": "pro_c1",
            "order_num": f"retellis:{uid}:plus_ww:deadbeef",
            "payment_status": "order_denied",
        }
    )
    sub = await apply_prodamus_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "canceled"
    user = await auth.get_user(uid)
    assert user.credits_usd == pytest.approx(10.0)  # unchanged — no new grant
    assert len(await billing.list_invoices(user_id=uid)) == 1  # no new invoice


async def test_canceled_for_unknown_order_is_noop():
    auth, billing, uid = await _make_stores()
    ev = prodamus_parse_event(
        {
            "order_id": "pro_x",
            "order_num": f"retellis:{uid}:plus_ww:zzz",
            "payment_status": "order_canceled",
        }
    )
    assert await apply_prodamus_event(ev, store=billing, auth_store=auth) is None
    assert await billing.get_subscription(user_id=uid) is None


async def test_success_without_linkage_is_noop():
    auth, billing, uid = await _make_stores()
    ev = prodamus_parse_event(
        {"order_id": "pro_y", "order_num": "garbage", "payment_status": "success"}
    )
    assert await apply_prodamus_event(ev, store=billing, auth_store=auth) is None
    user = await auth.get_user(uid)
    assert user.credits_usd == 0.0


async def test_idempotency_second_delivery_does_not_re_grant():
    auth, billing, uid = await _make_stores()
    await apply_prodamus_event(_success_event(uid), store=billing, auth_store=auth)
    first = await billing.mark_webhook_processed(provider="prodamus", event_id="pro_deadbeef")
    second = await billing.mark_webhook_processed(provider="prodamus", event_id="pro_deadbeef")
    assert first is True
    assert second is False


# --- endpoint: Sign is the ONLY auth ---


def _prodamus_app_kwargs():
    return dict(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        PUBLIC_ORIGIN="https://app.example.com",
        FEATURE_BILLING="1",
        PRODAMUS_SECRET_KEY=_SECRET,
        PRODAMUS_PAYFORM_URL="https://demo.payform.ru",
        PRODAMUS_SYS="retellis",
    )


async def test_endpoint_rejects_bad_signature(make_app, app_client):
    app = make_app(**_prodamus_app_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        body = json.dumps({"order_id": "p1", "order_num": "x", "submit": {}})
        r = await ac.post(
            "/v1/billing/webhook/prodamus",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401


async def test_endpoint_grants_on_verified_success(make_app, app_client):
    app = make_app(**_prodamus_app_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        user = await app.state.auth_store.create_user(
            issuer="local",
            subject="u-1",
            email="u@x.com",
            display_name="U",
            password_hash=None,
            plan="hosted_free",
            credits_usd=0.0,
        )
        uid = user.id
        submit = _submit(order_num=f"retellis:{uid}:plus_ww:deadbeef", order_id="pro_e1")
        payload = {
            "order_id": "pro_e1",
            "order_num": submit["order_num"],
            "payment_status": "success",
            "sum": "12.00",
            "currency": "usd",
            "submit": submit,
        }
        sign = _prodamus_sign(submit, _SECRET)
        r = await ac.post(
            "/v1/billing/webhook/prodamus",
            content=json.dumps(payload),
            headers={"Sign": sign, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        user = await app.state.auth_store.get_user(uid)
        assert user.plan == "plus_ww"
        assert user.credits_usd == pytest.approx(10.0)
        invs = await app.state.billing_store.list_invoices(user_id=uid)
        assert len(invs) == 1 and invs[0].provider == "prodamus"


async def test_endpoint_idempotent_on_redelivery(make_app, app_client):
    app = make_app(**_prodamus_app_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        user = await app.state.auth_store.create_user(
            issuer="local",
            subject="u-1",
            email="u@x.com",
            display_name="U",
            password_hash=None,
            plan="hosted_free",
            credits_usd=0.0,
        )
        uid = user.id
        submit = _submit(order_num=f"retellis:{uid}:plus_ww:deadbeef", order_id="pro_e2")
        payload = {
            "order_id": "pro_e2",
            "order_num": submit["order_num"],
            "payment_status": "success",
            "sum": "12.00",
            "currency": "usd",
            "submit": submit,
        }
        sign = _prodamus_sign(submit, _SECRET)
        body = json.dumps(payload)
        r1 = await ac.post(
            "/v1/billing/webhook/prodamus",
            content=body,
            headers={"Sign": sign, "Content-Type": "application/json"},
        )
        r2 = await ac.post(
            "/v1/billing/webhook/prodamus",
            content=body,
            headers={"Sign": sign, "Content-Type": "application/json"},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        user = await app.state.auth_store.get_user(uid)
        assert user.credits_usd == pytest.approx(10.0)  # not double-granted
