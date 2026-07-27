"""Billing checkout + plan catalogue + subscription read endpoints.

Checkout requires a verified Principal (401 without one — the insecure X-User-Id
header is OFF in these hosted apps). The purchase is a redirect to the
provider's hosted checkout; provider routing is by ``billing_country`` (RU →
ЮKassa, else Paddle), NOT by IP. A WW plan can't be bought via ЮKassa and vice
versa (geo mismatch → 400). An unconfigured provider → 503 (honest: the plan
isn't buyable yet). The happy path is tested with the outbound provider call
monkeypatched.
"""

from __future__ import annotations

import urllib.parse

from ai_companion_contracts import BillingProvider
from ai_companion_contracts import CheckoutSession as _CS

from ai_companion_api.auth.backends.magic_link import MagicLinkBackend


class _CaptureTransport:
    def __init__(self):
        self.links: list[tuple[str, str]] = []

    async def send(self, *, to: str, link: str) -> None:
        self.links.append((to, link))


def _hosted_kwargs(
    *, with_paddle: bool = True, with_yukassa: bool = False, with_prodamus: bool = False
) -> dict:
    kw = dict(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        PUBLIC_ORIGIN="https://app.example.com",
        FEATURE_BILLING="1",
    )
    if with_paddle:
        kw["PADDLE_API_KEY"] = "paddle_live_test_secret_value_0123456789"
    if with_yukassa:
        kw["YUKASSA_SHOP_ID"] = "123456"
        kw["YUKASSA_SECRET_KEY"] = "yukassa_live_test_secret_value_0123456789"
    if with_prodamus:
        kw["PRODAMUS_SECRET_KEY"] = "prodamus_live_test_secret_value_0123456789"
        kw["PRODAMUS_PAYFORM_URL"] = "https://demo.payform.ru"
        kw["PRODAMUS_SYS"] = "stillside"
    return kw


async def _sign_in(ac, app, email="buyer@example.com") -> None:
    """Sign in via magic link so the hosted client carries a session cookie."""
    capture = _CaptureTransport()
    app.state.auth_backend = MagicLinkBackend(
        app.state.settings, app.state.auth_store, transport=capture
    )
    await ac.post("/v1/auth/magiclink", json={"email": email})
    assert capture.links
    token = urllib.parse.parse_qs(urllib.parse.urlparse(capture.links[0][1]).query)["token"][0]
    cb = await ac.get("/v1/auth/magiclink/verify", params={"token": token})
    assert cb.status_code == 303


# --- plan catalogue ---


async def test_list_plans_geo_filter(make_app, app_client):
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        ww = await ac.get("/v1/billing/plans?geo=WW")
        assert ww.status_code == 200
        slugs = {p["slug"] for p in ww.json()}
        assert slugs == {"plus_ww", "pro_ww"}
        ru = await ac.get("/v1/billing/plans?geo=RU")
        assert {p["slug"] for p in ru.json()} == {"plus_ru", "pro_ru"}
        # No geo filter → both.
        allp = await ac.get("/v1/billing/plans")
        assert len(allp.json()) == 4


async def test_list_plans_empty_when_billing_off(client):
    # The default `client` fixture is self_hosted → billing off → empty list.
    r = await client.get("/v1/billing/plans")
    assert r.status_code == 200
    assert r.json() == []


# --- checkout auth + validation ---


async def test_checkout_requires_principal(make_app, app_client):
    # Insecure header OFF (make_app default) + no session → 401.
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ww", "billing_country": "WW"},
        )
        assert r.status_code == 401


async def test_checkout_404_when_billing_off(client):
    r = await client.post(
        "/v1/billing/checkout",
        json={"plan_slug": "plus_ww", "billing_country": "WW"},
    )
    assert r.status_code == 404


async def test_checkout_plan_not_found(make_app, app_client):
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "nonexistent", "billing_country": "WW"},
        )
        assert r.status_code == 404


async def test_checkout_geo_mismatch_rejected(make_app, app_client):
    # A WW plan can't be bought via ЮKassa (RU country) — disclose, don't
    # send the user to a checkout that can't complete.
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ww", "billing_country": "RU"},
        )
        assert r.status_code == 400


async def test_checkout_503_when_provider_unconfigured(make_app, app_client):
    # ЮKassa only (no Paddle) → a WW plan checkout routes to Paddle, which is
    # unconfigured → 503.
    app = make_app(**_hosted_kwargs(with_paddle=False, with_yukassa=True))
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ww", "billing_country": "WW"},
        )
        assert r.status_code == 503


# --- happy path (outbound provider call monkeypatched) ---


async def test_checkout_happy_path_paddle(make_app, app_client, monkeypatch):
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)

        async def fake_checkout(*, plan, user_id, return_url, settings):
            return _CS(
                redirect_url="https://sandbox-checkout.paddle.com/abc",
                provider=BillingProvider.paddle,
                provider_sub_id=None,
            )

        monkeypatch.setattr(
            "ai_companion_api.routers.billing.paddle_create_checkout", fake_checkout
        )
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ww", "billing_country": "WW"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["redirect_url"] == "https://sandbox-checkout.paddle.com/abc"
        assert body["provider"] == "paddle"


async def test_checkout_happy_path_yookassa(make_app, app_client, monkeypatch):
    app = make_app(**_hosted_kwargs(with_paddle=False, with_yukassa=True))
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)

        async def fake_checkout(*, plan, user_id, return_url, settings):
            return _CS(
                redirect_url="https://yookassa.ru/pay/abc",
                provider=BillingProvider.yookassa,
                provider_sub_id="pay-1",
            )

        monkeypatch.setattr(
            "ai_companion_api.routers.billing.yukassa_create_checkout", fake_checkout
        )
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ru", "billing_country": "RU"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "yookassa"


async def test_checkout_happy_path_prodamus(make_app, app_client, monkeypatch):
    # Prodamus-only deployment (no Paddle) — WW routes to Prodamus. A NULL
    # provider_price_id must NOT block (Prodamus builds the link from name+price).
    app = make_app(**_hosted_kwargs(with_paddle=False, with_prodamus=True))
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)

        async def fake_checkout(*, plan, user_id, return_url, settings):
            return _CS(
                redirect_url="https://demo.payform.ru/u8zDE/",
                provider=BillingProvider.prodamus,
                provider_sub_id=f"stillside-{user_id}-plus_ww-deadbeef",
            )

        monkeypatch.setattr(
            "ai_companion_api.routers.billing.prodamus_create_checkout", fake_checkout
        )
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ww", "billing_country": "WW"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["redirect_url"] == "https://demo.payform.ru/u8zDE/"
        assert body["provider"] == "prodamus"


async def test_checkout_prodamus_serves_ru_when_no_yookassa(make_app, app_client, monkeypatch):
    # RU country + no ЮKassa + Prodamus configured → routes to Prodamus (RU cards/SBP).
    app = make_app(**_hosted_kwargs(with_paddle=False, with_prodamus=True))
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)

        seen: dict[str, object] = {}

        async def fake_checkout(*, plan, user_id, return_url, settings):
            seen["plan_slug"] = plan.slug
            return _CS(
                redirect_url="https://demo.payform.ru/ru/",
                provider=BillingProvider.prodamus,
                provider_sub_id="x",
            )

        monkeypatch.setattr(
            "ai_companion_api.routers.billing.prodamus_create_checkout", fake_checkout
        )
        r = await ac.post(
            "/v1/billing/checkout",
            json={"plan_slug": "plus_ru", "billing_country": "RU"},
        )
        assert r.status_code == 200
        assert r.json()["provider"] == "prodamus"
        assert seen["plan_slug"] == "plus_ru"


# --- subscription read ---


async def test_get_subscription_none_on_free_tier(make_app, app_client):
    app = make_app(**_hosted_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        await _sign_in(ac, app)
        r = await ac.get("/v1/billing/subscription")
        assert r.status_code == 200
        assert r.json() is None


async def test_get_subscription_none_when_billing_off(client):
    r = await client.get("/v1/billing/subscription")
    assert r.status_code == 200
    assert r.json() is None
