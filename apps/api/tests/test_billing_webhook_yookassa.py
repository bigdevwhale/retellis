"""ЮKassa webhook: re-fetch verification, idempotency, 54-ФЗ receipt, state machine.

The notification body is NOT trusted alone — the handler re-fetches the payment
from the ЮKassa API (HTTP Basic) and acts on the authoritative status. A fetch
failure → skip (200), never grant credits on an unverified payment. ``payment.succeeded``
→ active + ``set_user_plan`` (plan + additive credit top-up) + an invoice row
carrying the 54-ФЗ ``fiscal_receipt_id``. ``payment.canceled`` → canceled, no grant.
"""

from __future__ import annotations

import json

import pytest

from ai_companion_api.auth.store import InMemoryAuthStore
from ai_companion_api.billing.providers import (
    fiscal_id_from_receipt,
    yukassa_event_from_payment,
    yukassa_fetch_payment,
    yukassa_fetch_receipt,
    yukassa_parse_notification,
)
from ai_companion_api.billing.store import InMemoryBillingStore
from ai_companion_api.routers.billing import apply_yookassa_event

_SETTINGS_KW = dict(
    yukassa_shop_id="123456",
    yukassa_secret_key="yukassa_live_test_secret_value_0123456789",
)


def _payment_dict(
    *,
    pid: str = "pay-1",
    status: str = "succeeded",
    plan_slug: str = "plus_ru",
    user_id: str = "u-1",
    receipt: str | None = "succeeded",
    saved: bool = True,
) -> dict:
    return {
        "id": pid,
        "status": status,
        "amount": {"value": "690.00", "currency": "RUB"},
        "payment_method": {"id": "pm-recurring-1", "saved": saved},
        "receipt_registration": receipt,
        "metadata": {"user_id": user_id, "plan_slug": plan_slug},
    }


def _notification(event: str, pid: str = "pay-1") -> dict:
    return {"event": event, "object": _payment_dict(pid=pid)}


# --- parsing ---


def test_parse_notification_succeeded():
    pre = yukassa_parse_notification(_notification("payment.succeeded"))
    assert pre is not None
    assert pre.event_type == "payment.succeeded"
    assert pre.payment_id == "pay-1"
    assert pre.amount_cents == 69000  # 690.00 RUB → kopecks


def test_parse_notification_ignores_unrefunded_events():
    # refund.succeeded is not a handled event → None.
    assert yukassa_parse_notification(_notification("refund.succeeded")) is None


def test_event_from_payment_carries_fiscal_receipt():
    # The fiscal id is NOT read from the payment object (which only has the
    # `receipt_registration` status) — it's passed in from the receipt fetch.
    ev = yukassa_event_from_payment(
        _payment_dict(), "payment.succeeded", fiscal_receipt_id="fiscal-12345"
    )
    assert ev.fiscal_receipt_id == "fiscal-12345"
    assert ev.metadata["plan_slug"] == "plus_ru"
    assert ev.recurring_payment_id == "pm-recurring-1"  # saved → recurring token


def test_event_from_payment_recurring_only_when_saved():
    # A one-off payment (save_payment_method=false) has no recurring token.
    ev = yukassa_event_from_payment(_payment_dict(saved=False), "payment.succeeded")
    assert ev.recurring_payment_id is None


def test_fiscal_id_from_receipt_extracts_document_number():
    assert fiscal_id_from_receipt({"fiscal_document_number": "12345"}) == "12345"
    # A pending receipt (not yet registered) has no fiscal number.
    assert fiscal_id_from_receipt({"status": "pending"}) is None
    assert fiscal_id_from_receipt(None) is None


# --- receipt fetch (injectable fetcher) ---


async def test_fetch_receipt_uses_injected_fetcher():
    from ai_companion_api.config import Settings

    settings = Settings(**_SETTINGS_KW)

    async def fake_fetcher(payment_id, s):
        return {"status": "succeeded", "fiscal_document_number": "12345"}

    out = await yukassa_fetch_receipt("pay-1", settings, fetcher=fake_fetcher)
    assert out == {"status": "succeeded", "fiscal_document_number": "12345"}


async def test_fetch_receipt_returns_none_on_failure():
    from ai_companion_api.config import Settings

    settings = Settings(**_SETTINGS_KW)

    async def failing_fetcher(payment_id, s):
        return None

    assert await yukassa_fetch_receipt("pay-1", settings, fetcher=failing_fetcher) is None


# --- re-fetch verification (injectable fetcher) ---


async def test_fetch_payment_uses_injected_fetcher():
    from ai_companion_api.config import Settings

    settings = Settings(**_SETTINGS_KW)
    fetched: dict[str, object] = {}

    async def fake_fetcher(payment_id, s):
        fetched["id"] = payment_id
        return _payment_dict(pid=payment_id)

    out = await yukassa_fetch_payment("pay-1", settings, fetcher=fake_fetcher)
    assert out is not None
    assert out["id"] == "pay-1"
    assert fetched["id"] == "pay-1"


async def test_fetch_payment_returns_none_on_fetch_failure():
    from ai_companion_api.config import Settings

    settings = Settings(**_SETTINGS_KW)

    async def failing_fetcher(payment_id, s):
        return None

    assert await yukassa_fetch_payment("pay-1", settings, fetcher=failing_fetcher) is None


# --- state machine ---


async def _make_stores(subject: str = "u-1"):
    """Create a user and return (auth, billing, real_user_id).

    ``create_user`` mints a UUID ``id`` (NOT the subject); the webhook event's
    ``user_id`` metadata must carry that real id, since ``set_user_plan`` /
    ``get_user`` resolve by id. Returning it lets tests thread the same id into
    both the event and the assertions.
    """
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


async def test_payment_succeeded_grants_plan_credits_and_fiscal_receipt():
    auth, billing, uid = await _make_stores()
    ev = yukassa_event_from_payment(
        _payment_dict(user_id=uid), "payment.succeeded", fiscal_receipt_id="fiscal-12345"
    )
    sub = await apply_yookassa_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    assert sub.provider_sub_id == "pm-recurring-1"  # saved → recurring token
    user = await auth.get_user(uid)
    assert user.plan == "plus_ru"
    assert user.credits_usd == pytest.approx(10.0)  # plus_ru credits grant
    invs = await billing.list_invoices(user_id=uid)
    assert len(invs) == 1
    assert invs[0].fiscal_receipt_id == "fiscal-12345"  # 54-ФЗ document number
    assert invs[0].currency == "RUB"


async def test_payment_canceled_no_grant():
    auth, billing, uid = await _make_stores()
    ev = yukassa_event_from_payment(
        _payment_dict(user_id=uid, status="canceled"), "payment.canceled"
    )
    sub = await apply_yookassa_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "canceled"
    user = await auth.get_user(uid)
    assert user.plan == "hosted_free"
    assert user.credits_usd == 0.0
    # No invoice on a cancellation.
    assert await billing.list_invoices(user_id=uid) == []


async def test_event_without_metadata_is_noop():
    auth, billing, uid = await _make_stores()
    ev = yukassa_event_from_payment(_payment_dict(user_id=uid), "payment.succeeded")
    ev.metadata = {}  # strip the linkage
    assert await apply_yookassa_event(ev, store=billing, auth_store=auth) is None
    user = await auth.get_user(uid)
    assert user.credits_usd == 0.0


# --- endpoint: re-fetch is the trust gate ---


def _yukassa_app_kwargs():
    return dict(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        PUBLIC_ORIGIN="https://app.example.com",
        FEATURE_BILLING="1",
        YUKASSA_SHOP_ID="123456",
        YUKASSA_SECRET_KEY="yukassa_live_test_secret_value_0123456789",
    )


async def test_endpoint_grants_on_verified_succeeded_payment(make_app, app_client, monkeypatch):
    app = make_app(**_yukassa_app_kwargs())
    async with app_client(app, base_url="https://test") as ac:
        # Create the user the webhook will credit; capture the real id (create_user
        # mints a UUID, NOT the subject) so the event metadata carries a user_id
        # that set_user_plan can actually resolve.
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

        # Re-fetch returns the authoritative succeeded payment.
        async def fake_fetch(payment_id, s):
            return _payment_dict(pid=payment_id, user_id=uid, plan_slug="plus_ru")

        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_payment", fake_fetch)

        # Best-effort receipt fetch returns a registered 54-ФЗ receipt.
        async def fake_receipt(payment_id, s):
            return {"status": "succeeded", "fiscal_document_number": "fiscal-99"}

        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_receipt", fake_receipt)
        r = await ac.post(
            "/v1/billing/webhook/yookassa",
            content=json.dumps(_notification("payment.succeeded", "pay-1")),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        user = await app.state.auth_store.get_user(uid)
        assert user.plan == "plus_ru"
        assert user.credits_usd == pytest.approx(10.0)
        # The 54-ФЗ fiscal document number flows onto the invoice end-to-end.
        invs = await app.state.billing_store.list_invoices(user_id=uid)
        assert len(invs) == 1
        assert invs[0].fiscal_receipt_id == "fiscal-99"


async def test_endpoint_skips_when_refetch_fails_no_grant(make_app, app_client, monkeypatch):
    app = make_app(**_yukassa_app_kwargs())
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

        async def failing_fetch(payment_id, s):
            return None

        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_payment", failing_fetch)
        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_receipt", failing_fetch)
        r = await ac.post(
            "/v1/billing/webhook/yookassa",
            content=json.dumps(_notification("payment.succeeded", "pay-2")),
            headers={"Content-Type": "application/json"},
        )
        # 200 (ack so ЮKassa stops retrying) but NO entitlement granted.
        assert r.status_code == 200
        user = await app.state.auth_store.get_user(uid)
        assert user.plan == "hosted_free"
        assert user.credits_usd == 0.0


async def test_endpoint_idempotent_on_redelivery(make_app, app_client, monkeypatch):
    app = make_app(**_yukassa_app_kwargs())
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

        async def fake_fetch(payment_id, s):
            return _payment_dict(pid=payment_id, user_id=uid, plan_slug="plus_ru")

        async def no_receipt(payment_id, s):
            return None

        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_payment", fake_fetch)
        monkeypatch.setattr("ai_companion_api.routers.billing.yukassa_fetch_receipt", no_receipt)
        body = json.dumps(_notification("payment.succeeded", "pay-3"))
        r1 = await ac.post("/v1/billing/webhook/yookassa", content=body)
        r2 = await ac.post("/v1/billing/webhook/yookassa", content=body)
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Redelivery did not double-grant.
        user = await app.state.auth_store.get_user(uid)
        assert user.credits_usd == pytest.approx(10.0)
