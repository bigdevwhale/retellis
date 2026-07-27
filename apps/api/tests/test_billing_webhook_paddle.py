"""Paddle webhook: signature verification, idempotency, subscription state machine.

The webhook route is unauthenticated in ``AuthMiddleware`` (``_PUBLIC_POST``) —
HMAC-SHA256 signature verification inside the handler is the ONLY auth. The
state machine (``apply_paddle_event``) is tested directly with the in-memory
stores against REALISTIC Paddle Billing payloads (the entity lives at ``data``,
not ``data.subscription``), and the endpoint is tested for the 401/200 signature
path. A successful payment calls ``AuthStore.set_user_plan`` (atomic plan +
additive credit top-up); ``transaction.completed`` is the ONLY granting event;
``subscription.*`` lifecycle events update status without granting
(``subscription.created`` → entity status, ``subscription.past_due`` → past_due,
``subscription.canceled`` → canceled).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest

from ai_companion_api.auth.store import InMemoryAuthStore
from ai_companion_api.billing.providers import paddle_parse_event, paddle_verify_signature
from ai_companion_api.billing.store import InMemoryBillingStore
from ai_companion_api.routers.billing import apply_paddle_event

_SECRET = "paddle_wh_secret"


def _sign(body: bytes, secret: str = _SECRET, *, ts: int | None = None) -> str:
    ts = ts or int(datetime.now(UTC).timestamp())
    h1 = hmac.new(secret.encode(), f"{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def _sub_payload(
    *,
    event_type: str,
    event_id: str,
    sub_id: str = "sub_01hv8x29kz0t586xy6zn1a62ny",
    status: str = "active",
    user_id: str = "u-1",
    plan_slug: str = "plus_ww",
    period_end: str = "2026-12-01T00:00:00+00:00",
    scheduled_change: dict | None = None,
) -> dict:
    """A realistic Paddle Billing ``subscription.*`` payload: the subscription
    entity IS the ``data`` object (``data.id``, ``data.status``,
    ``data.custom_data``, ``data.current_billing_period``). There is no
    ``data.subscription`` nesting — that was the bug (B1)."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "data": {
            "id": sub_id,
            "status": status,
            "customer_id": "ctm_01hv6y1jedq4p1n0yqn5ba3ky4",
            "items": [{"price": {"id": "pri_plus_ww"}}],
            "current_billing_period": {
                "starts_at": "2026-11-01T00:00:00+00:00",
                "ends_at": period_end,
            },
            "scheduled_change": scheduled_change,
            "custom_data": {"user_id": user_id, "plan_slug": plan_slug},
        },
    }


def _txn_payload(
    *,
    event_id: str,
    sub_id: str = "sub_01hv8x29kz0t586xy6zn1a62ny",
    txn_id: str | None = None,
    user_id: str = "u-1",
    plan_slug: str = "plus_ww",
) -> dict:
    """A realistic Paddle Billing ``transaction.completed`` payload: ``data`` is
    the transaction, with a ``subscription_id`` reference + the propagated
    ``custom_data``. This is the AUTHORITATIVE payment signal (B2)."""
    return {
        "event_id": event_id,
        "event_type": "transaction.completed",
        "data": {
            "id": txn_id or f"txn_{event_id}",
            "status": "completed",
            "subscription_id": sub_id,
            "customer_id": "ctm_01hv6y1jedq4p1n0yqn5ba3ky4",
            "custom_data": {"user_id": user_id, "plan_slug": plan_slug},
        },
    }


# --- signature verification ---


def test_paddle_signature_valid():
    body = b'{"event_id":"e1","event_type":"subscription.created"}'
    assert paddle_verify_signature(body, _sign(body), _SECRET) is True


def test_paddle_signature_bad_secret_rejected():
    body = b'{"event_id":"e1"}'
    assert paddle_verify_signature(body, _sign(body, "wrong-secret"), _SECRET) is False


def test_paddle_signature_missing_header_rejected():
    assert paddle_verify_signature(b'{"event_id":"e1"}', "", _SECRET) is False


def test_paddle_signature_stale_ts_rejected():
    body = b'{"event_id":"e1"}'
    old_ts = int(datetime.now(UTC).timestamp()) - 3600  # 1h ago → outside 300s skew
    assert paddle_verify_signature(body, _sign(body, ts=old_ts), _SECRET) is False


# --- parser: entity lives at `data` (B1 regression) ---


def test_parse_subscription_event_reads_entity_at_data():
    # data IS the subscription — subscription_id / status / period come from
    # data.* directly, NOT data.subscription (which doesn't exist).
    ev = paddle_parse_event(_sub_payload(event_type="subscription.created", event_id="e1"))
    assert ev.subscription_id == "sub_01hv8x29kz0t586xy6zn1a62ny"
    assert ev.status == "active"
    assert ev.plan_id == "pri_plus_ww"
    assert ev.current_period_end == datetime.fromisoformat("2026-12-01T00:00:00+00:00")


def test_parse_transaction_event_reads_subscription_id_reference():
    ev = paddle_parse_event(_txn_payload(event_id="e9"))
    assert ev.event_type == "transaction.completed"
    assert ev.subscription_id == "sub_01hv8x29kz0t586xy6zn1a62ny"  # data.subscription_id
    assert ev.plan_id is None  # transactions don't carry items[].price


def test_parse_scheduled_cancel_flag():
    ev = paddle_parse_event(
        _sub_payload(
            event_type="subscription.updated",
            event_id="e1",
            scheduled_change={"type": "cancel", "effective_at": "2026-12-01T00:00:00+00:00"},
        )
    )
    assert ev.cancel_at_period_end is True


# --- state machine ---


async def _make_stores(subject: str = "u-1"):
    """Create a user and return (auth, billing, real_user_id).

    ``create_user`` mints a UUID ``id`` (NOT the subject); the event's
    ``custom_data.user_id`` must carry that real id, since ``set_user_plan`` /
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


async def test_subscription_created_trialing_when_trial():
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(
        _sub_payload(
            event_type="subscription.created", event_id="e1", status="trialing", user_id=uid
        )
    )
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub is not None
    assert sub.status.value == "trialing"
    # No entitlement granted on a bare creation (no payment yet).
    user = await auth.get_user(uid)
    assert user.plan == "hosted_free"
    assert user.credits_usd == 0.0


async def test_subscription_created_active_when_no_trial():
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(
        _sub_payload(event_type="subscription.created", event_id="e1", status="active", user_id=uid)
    )
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    user = await auth.get_user(uid)
    assert user.credits_usd == 0.0  # lifecycle event — never grants


async def test_transaction_completed_grants_plan_and_credits():
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(_txn_payload(event_id="e2", user_id=uid))
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    user = await auth.get_user(uid)
    assert user.plan == "plus_ww"
    # plus_ww credits_grant_usd = 10.0
    assert user.credits_usd == pytest.approx(10.0)
    # An invoice row was recorded, keyed on the Paddle transaction id.
    invs = await billing.list_invoices(user_id=uid)
    assert len(invs) == 1
    assert invs[0].status == "paid"
    assert invs[0].provider_invoice_id == "txn_e2"


async def test_renewal_credit_topup_is_additive():
    auth, billing, uid = await _make_stores()
    # First completed payment → +10.
    await apply_paddle_event(
        paddle_parse_event(_txn_payload(event_id="e2", user_id=uid)),
        store=billing,
        auth_store=auth,
    )
    # A second completed payment (renewal, same sub, new event id) → +10 more,
    # NOT a reset to 10.
    await apply_paddle_event(
        paddle_parse_event(_txn_payload(event_id="e3", user_id=uid)),
        store=billing,
        auth_store=auth,
    )
    user = await auth.get_user(uid)
    assert user.credits_usd == pytest.approx(20.0)


async def test_subscription_past_due_no_grant():
    # Real Paddle failure signal is `subscription.past_due` (there is no
    # `subscription.payment_failed`).
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(
        _sub_payload(
            event_type="subscription.past_due", event_id="e4", status="past_due", user_id=uid
        )
    )
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "past_due"
    user = await auth.get_user(uid)
    assert user.plan == "hosted_free"
    assert user.credits_usd == 0.0


async def test_subscription_updated_does_not_grant():
    # subscription.updated must NOT grant — only transaction.completed grants.
    # A non-payment update (plan change, quantity) firing .updated with
    # status=active must not mint credits.
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(
        _sub_payload(
            event_type="subscription.updated", event_id="e10", status="active", user_id=uid
        )
    )
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    user = await auth.get_user(uid)
    assert user.credits_usd == 0.0
    assert await billing.list_invoices(user_id=uid) == []


async def test_subscription_activated_sets_active_no_grant():
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(
        _sub_payload(
            event_type="subscription.activated", event_id="e11", status="active", user_id=uid
        )
    )
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub.status.value == "active"
    user = await auth.get_user(uid)
    assert user.credits_usd == 0.0


async def test_subscription_canceled_sets_canceled():
    auth, billing, uid = await _make_stores()
    # Active + paid first.
    await apply_paddle_event(
        paddle_parse_event(_txn_payload(event_id="e2", user_id=uid)),
        store=billing,
        auth_store=auth,
    )
    sub = await apply_paddle_event(
        paddle_parse_event(
            _sub_payload(
                event_type="subscription.canceled", event_id="e5", status="canceled", user_id=uid
            )
        ),
        store=billing,
        auth_store=auth,
    )
    assert sub.status.value == "canceled"
    # Plan dangles until period end — NOT reset to free on the canceled event.
    user = await auth.get_user(uid)
    assert user.plan == "plus_ww"


async def test_transaction_without_subscription_id_is_noop():
    auth, billing, uid = await _make_stores()
    payload = _txn_payload(event_id="e6", user_id=uid)
    payload["data"]["subscription_id"] = None  # one-off transaction, no sub
    ev = paddle_parse_event(payload)
    assert await apply_paddle_event(ev, store=billing, auth_store=auth) is None


async def test_transaction_completed_persists_customer_id_on_profile():
    # The webhook carries data.customer_id (Paddle creates the customer at
    # checkout); we persist it onto the billing profile so the customer portal
    # can open an authenticated session.
    auth, billing, uid = await _make_stores()
    await billing.upsert_billing_profile(user_id=uid, country="WW", provider="paddle")
    ev = paddle_parse_event(_txn_payload(event_id="e2", user_id=uid))
    await apply_paddle_event(ev, store=billing, auth_store=auth)
    profile = await billing.get_billing_profile(user_id=uid)
    assert profile is not None
    assert profile.provider_customer_id == "ctm_01hv6y1jedq4p1n0yqn5ba3ky4"


async def test_subscription_created_persists_customer_id_on_profile():
    auth, billing, uid = await _make_stores()
    await billing.upsert_billing_profile(user_id=uid, country="WW", provider="paddle")
    ev = paddle_parse_event(
        _sub_payload(event_type="subscription.created", event_id="e1", status="active", user_id=uid)
    )
    await apply_paddle_event(ev, store=billing, auth_store=auth)
    profile = await billing.get_billing_profile(user_id=uid)
    assert profile is not None
    assert profile.provider_customer_id == "ctm_01hv6y1jedq4p1n0yqn5ba3ky4"


async def test_customer_id_persist_is_best_effort_without_profile():
    # A webhook arriving with no prior billing profile (e.g. subscription
    # imported) is a no-op for the customer-id attach — it must NOT crash.
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(_txn_payload(event_id="e2", user_id=uid))
    sub = await apply_paddle_event(ev, store=billing, auth_store=auth)
    assert sub is not None  # grant still happened
    assert await billing.get_billing_profile(user_id=uid) is None  # no profile to attach to


async def test_idempotency_second_delivery_does_not_re_grant():
    auth, billing, uid = await _make_stores()
    ev = paddle_parse_event(_txn_payload(event_id="e2", user_id=uid))
    await apply_paddle_event(ev, store=billing, auth_store=auth)
    # The idempotency guard is the store's mark_webhook_processed — a second
    # mark returns False (the handler would ack without re-applying).
    first = await billing.mark_webhook_processed(provider="paddle", event_id="e2")
    second = await billing.mark_webhook_processed(provider="paddle", event_id="e2")
    assert first is True
    assert second is False


# --- endpoint: signature is the ONLY auth ---


async def test_endpoint_rejects_bad_signature(make_app, app_client):
    app = make_app(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        PUBLIC_ORIGIN="https://app.example.com",
        FEATURE_BILLING="1",
        PADDLE_API_KEY="paddle_live_test_secret_value_0123456789",
        PADDLE_WEBHOOK_SECRET=_SECRET,
    )
    async with app_client(app, base_url="https://test") as ac:
        body = json.dumps({"event_id": "e1", "event_type": "subscription.created"})
        # No Paddle-Signature header → 401.
        r = await ac.post("/v1/billing/webhook/paddle", content=body)
        assert r.status_code == 401


async def test_endpoint_accepts_valid_signature(make_app, app_client):
    app = make_app(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        PUBLIC_ORIGIN="https://app.example.com",
        FEATURE_BILLING="1",
        PADDLE_API_KEY="paddle_live_test_secret_value_0123456789",
        PADDLE_WEBHOOK_SECRET=_SECRET,
    )
    async with app_client(app, base_url="https://test") as ac:
        payload = {"event_id": "evt_99", "event_type": "subscription.created", "data": {}}
        body = json.dumps(payload).encode()
        r = await ac.post(
            "/v1/billing/webhook/paddle",
            content=body,
            headers={"Paddle-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}
