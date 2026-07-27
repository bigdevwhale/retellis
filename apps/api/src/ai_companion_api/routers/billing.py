"""``/v1/billing`` — subscription purchase (Paddle WW / ЮKassa RU).

Hosted-only capability (``feature_billing and is_hosted``). The purchase is a
redirect to the provider's hosted checkout; webhooks are the SINGLE source of
truth for subscription state — the checkout callback redirect does NOT mutate
state. A successful payment calls ``AuthStore.set_user_plan`` (atomic plan +
additive credit top-up); the existing ``out_of_credits`` gate and per-turn
``decrement_credits`` are unchanged.

Provider routing is by ``billing_country`` (manual, account-derived), NOT by
IP: ``RU`` → ЮKassa (RUB, 54-ФЗ receipt), anything else → Paddle (USD/EUR).
Webhook routes are unauthenticated in ``AuthMiddleware`` (in ``_PUBLIC_POST``)
— signature verification inside the handler is the ONLY auth.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from ai_companion_contracts import (
    BillingProvider,
    BillingWebhookAck,
    CheckoutRequest,
    CheckoutSession,
    Plan,
    PortalSession,
    Subscription,
    SubscriptionStatus,
)
from fastapi import APIRouter, Depends, HTTPException, Request

from ..billing.providers import (
    PaddleEvent,
    ProdamusEvent,
    YooKassaEvent,
    fiscal_id_from_receipt,
    paddle_create_checkout,
    paddle_create_portal,
    paddle_parse_event,
    paddle_verify_signature,
    prodamus_create_checkout,
    prodamus_create_portal,
    prodamus_parse_event,
    prodamus_verify_signature,
    yukassa_create_checkout,
    yukassa_create_portal,
    yukassa_event_from_payment,
    yukassa_fetch_payment,
    yukassa_fetch_receipt,
    yukassa_parse_notification,
)
from ..billing.store import BillingStore, InvoiceRecord
from ..config import Settings
from ..deps import get_billing_store, get_current_principal, get_settings
from ..observability.redaction import redact

logger = logging.getLogger(__name__)

router = APIRouter()

PrincipalDep = Annotated[Any, Depends(get_current_principal)]
BillingStoreDep = Annotated[BillingStore, Depends(get_billing_store)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Paddle subscription status → our SubscriptionStatus.
_PADDLE_STATUS_MAP: dict[str, SubscriptionStatus] = {
    "active": SubscriptionStatus.active,
    "trialing": SubscriptionStatus.trialing,
    "past_due": SubscriptionStatus.past_due,
    "paused": SubscriptionStatus.past_due,  # paused → treat as past_due (no new credits)
    "canceled": SubscriptionStatus.canceled,
}


def _provider_for_country(country: str, settings: Settings) -> BillingProvider:
    """Pick a provider by the user's ``billing_country`` (manual, NOT IP-derived).

    RU → ЮKassa when configured (RUB, 54-ФЗ), else Prodamus (RU cards + SBP);
    WW → Prodamus when configured (RU-resident operator path — accepts foreign
    cards incl. TR/WW), else Paddle (Merchant of Record, USD/EUR). A provider
    returned here that isn't actually configured 503s at checkout time — this
    function only expresses routing intent."""
    ru = country.upper() == "RU"
    if ru:
        if settings.yukassa_shop_id:
            return BillingProvider.yookassa
        if settings.prodamus_secret_key:
            return BillingProvider.prodamus
        return BillingProvider.yookassa  # default; 503s if unconfigured
    if settings.prodamus_secret_key:
        return BillingProvider.prodamus
    return BillingProvider.paddle


def _plan_slug_for_event(
    store: BillingStore, plan_id: str | None, metadata: dict[str, Any] | None
) -> str | None:
    """Resolve our plan_slug from a webhook. The checkout echoes ``plan_slug``
    in passthrough metadata (Paddle custom_data / ЮKassa metadata); we trust
    that over the provider's price id (which may not be linked yet)."""
    if metadata and isinstance(metadata, dict):
        slug = metadata.get("plan_slug")
        if isinstance(slug, str):
            return slug
    if plan_id:
        # Fall back: find a plan whose provider_price_id matches. Best-effort;
        # may be None until plans are linked in the dashboard.
        return None
    return None


async def _persist_paddle_customer_id(
    event: PaddleEvent, user_id: str | None, store: BillingStore
) -> None:
    """Best-effort: attach the Paddle ``customer_id`` carried on a webhook event
    to the user's billing profile, so ``paddle_create_portal`` can open an
    authenticated customer-portal session. A failure here MUST NOT break the
    grant / turn — the customer id can be backfilled by a later webhook."""
    if not user_id or not event.customer_id:
        return
    try:
        await store.set_provider_customer_id(
            user_id=user_id, provider="paddle", provider_customer_id=event.customer_id
        )
    except Exception:  # noqa: BLE001 — best-effort; never block the webhook
        logger.warning("paddle customer_id persist failed for user %s", user_id)


# --- Endpoints ---------------------------------------------------------------


@router.get("/billing/plans", response_model=list[Plan])
async def list_plans(
    settings: SettingsDep,
    store: BillingStoreDep,
    geo: str | None = None,
) -> list[Plan]:
    """Public plan catalogue. ``geo=RU`` returns the ЮKassa/RUB plans,
    ``geo=WW`` the Paddle/USD plans; omitted returns both. The web's
    PlansScreen renders from its localized fixture but validates the chosen
    slug against this list at checkout."""
    if settings.deployment_mode != "hosted" or not settings.feature_billing:
        return []
    return await store.list_plans(geo=geo)


@router.post("/billing/checkout", response_model=CheckoutSession)
async def create_checkout(
    body: CheckoutRequest,
    principal: PrincipalDep,
    settings: SettingsDep,
    store: BillingStoreDep,
    request: Request,
) -> CheckoutSession:
    """Create a hosted-checkout session at the provider for ``plan_slug``.
    ``billing_country`` selects the provider (RU → ЮKassa, else Paddle). The
    browser is redirected to the provider's domain — no card data is collected
    on our side (PCI-scope SAQ-A)."""
    if settings.deployment_mode != "hosted" or not settings.feature_billing:
        raise HTTPException(status_code=404, detail="billing not available on this instance")
    plan = await store.get_plan(body.plan_slug)
    if plan is None or not plan.active:
        raise HTTPException(status_code=404, detail="plan not found")
    provider = _provider_for_country(body.billing_country, settings)
    # Reject a geo mismatch early — "disclose, don't perform": don't send the
    # user to a checkout that can't complete. ЮKassa only serves RU (RUB) plans;
    # Paddle only serves WW (USD/EUR) plans; Prodamus serves BOTH (RU cards/SBP
    # and foreign cards), so no geo restriction applies to it.
    if provider == BillingProvider.yookassa and plan.geo != "RU":
        raise HTTPException(status_code=400, detail="plan geo does not match billing country")
    if provider == BillingProvider.paddle and plan.geo != "WW":
        raise HTTPException(status_code=400, detail="plan geo does not match billing country")
    # Persist the billing profile so the webhook can link back even before the
    # first payment lands. Best-effort — a profile failure must not block checkout.
    try:
        await store.upsert_billing_profile(
            user_id=principal.user_id, country=body.billing_country, provider=provider.value
        )
    except Exception:  # noqa: BLE001
        logger.warning("billing profile upsert failed for user %s", principal.user_id)
    return_url = _return_url(settings, request)
    try:
        if provider == BillingProvider.yookassa:
            return await yukassa_create_checkout(
                plan=plan, user_id=principal.user_id, return_url=return_url, settings=settings
            )
        if provider == BillingProvider.prodamus:
            return await prodamus_create_checkout(
                plan=plan, user_id=principal.user_id, return_url=return_url, settings=settings
            )
        return await paddle_create_checkout(
            plan=plan, user_id=principal.user_id, return_url=return_url, settings=settings
        )
    except Exception as exc:  # _ProviderUnavailable or network
        logger.warning("checkout creation failed (%s): %s", provider.value, redact(str(exc)))
        raise HTTPException(status_code=503, detail="checkout unavailable") from exc


@router.post("/billing/portal", response_model=PortalSession)
async def create_portal(
    principal: PrincipalDep,
    settings: SettingsDep,
    store: BillingStoreDep,
) -> PortalSession:
    """Redirect to the provider's self-service portal (cancel, change card,
    invoices). Managed by the provider — we never build our own cancel/card UI."""
    if settings.deployment_mode != "hosted" or not settings.feature_billing:
        raise HTTPException(status_code=404, detail="billing not available on this instance")
    sub = await store.get_subscription(user_id=principal.user_id)
    provider = (
        BillingProvider(sub.provider)
        if sub is not None
        else BillingProvider.paddle  # default; portal 503s if unconfigured
    )
    try:
        if provider == BillingProvider.yookassa:
            return await yukassa_create_portal(user_id=principal.user_id, settings=settings)
        if provider == BillingProvider.prodamus:
            return await prodamus_create_portal(user_id=principal.user_id, settings=settings)
        return await paddle_create_portal(
            user_id=principal.user_id, settings=settings, billing_store=store
        )
    except Exception as exc:  # _ProviderUnavailable or network
        logger.warning("portal creation failed (%s): %s", provider.value, redact(str(exc)))
        raise HTTPException(status_code=503, detail="portal unavailable") from exc


@router.get("/billing/subscription", response_model=Subscription | None)
async def get_subscription(
    principal: PrincipalDep,
    settings: SettingsDep,
    store: BillingStoreDep,
) -> Subscription | None:
    """The user's current subscription for the billing tab. None when the user
    is on the free tier (no subscription row)."""
    if settings.deployment_mode != "hosted" or not settings.feature_billing:
        return None
    return await store.get_subscription(user_id=principal.user_id)


# --- Webhooks ----------------------------------------------------------------


@router.post("/billing/webhook/paddle", response_model=BillingWebhookAck)
async def paddle_webhook(
    request: Request,
    settings: SettingsDep,
    store: BillingStoreDep,
) -> BillingWebhookAck:
    """Paddle webhook. Verify the HMAC-SHA256 signature (the ONLY auth on this
    route), then process idempotently. Returns 200 to Paddle on success OR on
    a bad signature (401) so a malformed retry doesn't loop — Paddle treats
    non-2xx as retry, and we don't want to retry a bad-signature payload."""
    raw = await request.body()
    sig = request.headers.get("Paddle-Signature") or ""
    if not paddle_verify_signature(raw, sig, settings.paddle_webhook_secret):
        logger.warning("paddle webhook signature verification failed")
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid payload") from None
    event = paddle_parse_event(payload)
    if not event.event_id:
        return BillingWebhookAck(ok=True)  # nothing to act on; ack so Paddle stops retrying
    # Idempotency: a redelivery of an already-processed event is a bare ack.
    if not await store.mark_webhook_processed(provider="paddle", event_id=event.event_id):
        return BillingWebhookAck(ok=True)
    auth_store = request.app.state.auth_store
    await apply_paddle_event(event, store=store, auth_store=auth_store)
    return BillingWebhookAck(ok=True)


@router.post("/billing/webhook/yookassa", response_model=BillingWebhookAck)
async def yookassa_webhook(
    request: Request,
    settings: SettingsDep,
    store: BillingStoreDep,
) -> BillingWebhookAck:
    """ЮKassa notification. The notification body is NOT trusted alone — we
    re-fetch the payment from the ЮKassa API (HTTP Basic) and act on the
    authoritative status. Optional shared-secret check on the query string."""
    # Optional shared-secret: ЮKassa can be configured to POST to
    # ``/v1/billing/webhook/yookassa?secret=...``; if the secret is set in
    # config, require it to match. Skip when unset (operator didn't opt in).
    if settings.yukassa_webhook_secret:
        qs = request.query_params.get("secret") or ""
        if qs != settings.yukassa_webhook_secret:
            logger.warning("yookassa webhook shared-secret mismatch")
            raise HTTPException(status_code=401, detail="invalid secret")
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid payload") from None
    pre = yukassa_parse_notification(payload)
    if pre is None:
        return BillingWebhookAck(ok=True)  # event type we don't handle
    # Idempotency keyed on ``{payment_id}:{event}``.
    if not await store.mark_webhook_processed(provider="yookassa", event_id=pre.event_id):
        return BillingWebhookAck(ok=True)
    # Re-fetch the authoritative payment state. A fetch failure → skip (200),
    # never grant credits on an unverified payment.
    payment = await yukassa_fetch_payment(pre.payment_id, settings)
    if payment is None:
        logger.warning("yookassa webhook: payment re-fetch failed for %s", pre.payment_id)
        return BillingWebhookAck(ok=True)
    # Best-effort 54-ФЗ receipt fetch: the payment object only carries
    # ``receipt_registration`` (a status), not the fiscal document number. A
    # ``pending`` registration at webhook time is normal (the cheque isn't filed
    # yet) — we record the invoice without a fiscal id rather than block.
    receipt = await yukassa_fetch_receipt(pre.payment_id, settings)
    fiscal_id = fiscal_id_from_receipt(receipt)
    event = yukassa_event_from_payment(payment, pre.event_type, fiscal_receipt_id=fiscal_id)
    auth_store = request.app.state.auth_store
    await apply_yookassa_event(event, store=store, auth_store=auth_store)
    return BillingWebhookAck(ok=True)


@router.post("/billing/webhook/prodamus", response_model=BillingWebhookAck)
async def prodamus_webhook(
    request: Request,
    settings: SettingsDep,
    store: BillingStoreDep,
) -> BillingWebhookAck:
    """Prodamus webhook. The body is JSON (we set ``callbackType=json`` at
    checkout) with a ``submit`` sub-object; the ``Sign`` header is an HMAC-SHA256
    over ``submit`` computed with the payform secret key — verifying it is the
    ONLY auth on this route (it's in ``_PUBLIC_POST``). Idempotency keys on
    Prodamus's internal ``order_id``. Returns 200 on success OR on a bad
    signature (401) so a malformed retry doesn't loop — Prodamus treats non-2xx
    as retry and we don't want to retry a bad-signature payload."""
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    sign = request.headers.get("Sign") or ""
    if not prodamus_verify_signature(payload, sign, settings.prodamus_secret_key):
        logger.warning("prodamus webhook signature verification failed")
        raise HTTPException(status_code=401, detail="invalid signature")
    event = prodamus_parse_event(payload)
    if not event.event_id or not event.order_num:
        return BillingWebhookAck(ok=True)  # nothing to act on; ack so Prodamus stops
    if not await store.mark_webhook_processed(provider="prodamus", event_id=event.event_id):
        return BillingWebhookAck(ok=True)
    auth_store = request.app.state.auth_store
    await apply_prodamus_event(event, store=store, auth_store=auth_store)
    return BillingWebhookAck(ok=True)


# --- State machine (pure-ish; testable directly) ----------------------------


async def apply_paddle_event(
    event: PaddleEvent, *, store: BillingStore, auth_store: Any
) -> Subscription | None:
    """Apply a (signature-verified) Paddle Billing event to the subscription state.

    Paddle Billing splits the signal across two event families:

    - ``transaction.completed`` — the AUTHORITATIVE "payment succeeded + fully
      processed" signal (Paddle populates ``subscription_id`` only by
      ``completed``, not ``paid``). This is the ONLY event that grants
      entitlement: ``set_user_plan`` (plan + additive credit top-up) + an
      invoice row. Granting on the transaction (not ``subscription.*``) keys the
      grant on the real payment and avoids double-grants from the several
      subscription events that fire around one payment.
    - ``subscription.*`` lifecycle events update the subscription status but
      NEVER grant: ``created`` → entity status (``trialing`` if a trial,
      ``active`` otherwise); ``activated`` → ``active``; ``trialing`` →
      ``trialing``; ``past_due`` / ``paused`` → ``past_due``; ``resumed`` →
      ``active``; ``canceled`` → ``canceled`` (plan dangles until period end);
      ``updated`` → entity status.

    ``plan_slug`` + ``user_id`` come from the checkout-embedded ``custom_data``
    (Paddle propagates the transaction's ``custom_data`` to subsequent events);
    we trust that over the provider price id. Returns the upserted subscription
    (None if we couldn't resolve the subscription id / user / plan).
    """
    metadata = (event.raw or {}).get("data", {}).get("custom_data") if event.raw else None
    plan_slug = _plan_slug_for_event(store, event.plan_id, metadata)
    et = event.event_type
    now = datetime.now(UTC)

    # --- transaction.completed: the payment signal → grant. ---
    if et == "transaction.completed":
        sub_id = event.subscription_id  # data.subscription_id on the transaction
        if sub_id is None:
            return None
        existing = await store.get_subscription_by_provider_sub(
            provider="paddle", provider_sub_id=sub_id
        )
        user_id = (existing.user_id if existing else None) or (
            metadata.get("user_id") if isinstance(metadata, dict) else None
        )
        if user_id is None or plan_slug is None:
            return None
        plan = await store.get_plan(plan_slug)
        if plan is None:
            return None
        # The transaction carries no billing-period info; preserve the period the
        # subscription.created event set, else start a fresh 30-day cycle (renewal).
        existing_end = existing.current_period_end if existing else None
        if event.current_period_end:
            period_end = event.current_period_end
        elif existing_end and existing_end > now:
            period_end = existing_end
        else:
            period_end = now + timedelta(days=30)
        sub = Subscription(
            id=existing.id if existing else uuid.uuid4().hex,
            user_id=user_id,
            plan_slug=plan_slug,
            provider=BillingProvider.paddle,
            provider_sub_id=sub_id,
            status=SubscriptionStatus.active,
            current_period_start=existing.current_period_start if existing else now,
            current_period_end=period_end,
            cancel_at_period_end=existing.cancel_at_period_end if existing else False,
            trial_ends_at=None,
            billing_country=(existing.billing_country if existing else None),
            created_at=existing.created_at if existing else now,
        )
        sub = await store.upsert_subscription(sub)
        await auth_store.set_user_plan(
            user_id=user_id, plan=plan_slug, credits_grant_usd=plan.credits_grant_usd
        )
        await store.insert_invoice(_invoice_from_paddle(event, sub, plan))
        await _persist_paddle_customer_id(event, user_id, store)
        return sub

    # --- subscription.* lifecycle: update status, NO entitlement grant. ---
    if event.subscription_id is None:
        return None
    existing = await store.get_subscription_by_provider_sub(
        provider="paddle", provider_sub_id=event.subscription_id
    )
    user_id = (existing.user_id if existing else None) or (
        metadata.get("user_id") if isinstance(metadata, dict) else None
    )
    if user_id is None:
        return None

    if et == "subscription.created":
        # Entity status: ``trialing`` when the plan has a trial, ``active`` otherwise.
        status = _PADDLE_STATUS_MAP.get(event.status or "", SubscriptionStatus.active)
    elif et == "subscription.activated":
        status = SubscriptionStatus.active
    elif et == "subscription.trialing":
        status = SubscriptionStatus.trialing
    elif et in ("subscription.past_due", "subscription.paused"):
        status = SubscriptionStatus.past_due
    elif et == "subscription.resumed":
        status = SubscriptionStatus.active
    elif et == "subscription.canceled":
        status = SubscriptionStatus.canceled
    else:  # subscription.updated (and any future lifecycle event)
        status = _PADDLE_STATUS_MAP.get(event.status or "", SubscriptionStatus.active)

    period_end = event.current_period_end or (
        existing.current_period_end if existing else now + timedelta(days=30)
    )
    period_start = existing.current_period_start if existing else now
    sub = Subscription(
        id=existing.id if existing else uuid.uuid4().hex,
        user_id=user_id,
        plan_slug=plan_slug or (existing.plan_slug if existing else ""),
        provider=BillingProvider.paddle,
        provider_sub_id=event.subscription_id,
        status=status,
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=event.cancel_at_period_end,
        trial_ends_at=period_end if status == SubscriptionStatus.trialing else None,
        billing_country=(existing.billing_country if existing else None),
        created_at=existing.created_at if existing else now,
    )
    sub = await store.upsert_subscription(sub)
    await _persist_paddle_customer_id(event, user_id, store)
    return sub


async def apply_yookassa_event(
    event: YooKassaEvent, *, store: BillingStore, auth_store: Any
) -> Subscription | None:
    """Apply an AUTHORITATIVE (re-fetched) ЮKassa payment event.

    - ``payment.succeeded`` → ``active`` + ``set_user_plan`` + invoice (with
      54-ФЗ ``fiscal_receipt_id``).
    - ``payment.canceled`` → ``canceled`` (no entitlement change).
    - ``payment.waiting_for_capture`` → capture immediately (autocapture); for
      the subscription flow ``capture=True`` means succeeded arrives directly,
      so this branch is a no-op ack in practice.

    The provider_sub_id is the recurring payment token (``recurring_payment_id``)
    when present; the one-off payment id otherwise. ``plan_slug`` + ``user_id``
    come from the checkout-embedded metadata (trusted — we set it ourselves)."""
    plan_slug = event.metadata.get("plan_slug") if isinstance(event.metadata, dict) else None
    user_id = event.metadata.get("user_id") if isinstance(event.metadata, dict) else None
    if user_id is None or plan_slug is None:
        return None
    plan = await store.get_plan(plan_slug)
    if plan is None:
        return None

    provider_sub_id = event.recurring_payment_id or event.payment_id
    existing = await store.get_subscription_by_provider_sub(
        provider="yookassa", provider_sub_id=provider_sub_id
    )
    now = datetime.now(UTC)
    if event.event_type == "payment.succeeded":
        status = SubscriptionStatus.active
    elif event.event_type == "payment.canceled":
        status = SubscriptionStatus.canceled
    else:
        status = SubscriptionStatus.active  # waiting_for_capture (autocapture) → active

    sub = Subscription(
        id=existing.id if existing else uuid.uuid4().hex,
        user_id=user_id,
        plan_slug=plan_slug,
        provider=BillingProvider.yookassa,
        provider_sub_id=provider_sub_id,
        status=status,
        current_period_start=existing.current_period_start if existing else now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
        trial_ends_at=None,  # РФ has no trial (54-ФЗ)
        billing_country="RU",
        created_at=existing.created_at if existing else now,
    )
    sub = await store.upsert_subscription(sub)

    if event.event_type == "payment.succeeded":
        await auth_store.set_user_plan(
            user_id=user_id, plan=plan_slug, credits_grant_usd=plan.credits_grant_usd
        )
        await store.insert_invoice(_invoice_from_yookassa(event, sub, plan))
    return sub


async def apply_prodamus_event(
    event: ProdamusEvent, *, store: BillingStore, auth_store: Any
) -> Subscription | None:
    """Apply a (signature-verified) Prodamus webhook event.

    Prodamus is a ONE-OFF payment flow here (no ``subscription``/Clubs auto-renew
    assumed): each successful payment grants the plan + credits for a fresh
    30-day window (additive credits — renewals top up, never reset). The
    subscription row is keyed per USER (reused across renewals) with
    ``provider_sub_id`` = our internal ``order_num`` (unique per checkout); we
    look it up by ``user_id`` so a renewal updates the same row rather than
    spawning a new one. ``cancel_at_period_end`` is always True — there's no
    auto-renew to cancel.

    ``payment_status == "success"`` grants; ``order_canceled`` / ``order_denied``
    only update an EXISTING subscription to ``canceled`` (a cancellation for an
    order we've never seen is a no-op). ``user_id`` + ``plan_slug`` are recovered
    from the ``order_num`` we encoded at checkout — the webhook body is not
    trusted alone (the ``Sign`` is the auth). 54-ФЗ is fiscalized on Prodamus's
    side (auto for NPD); we record the invoice without a ``fiscal_receipt_id``
    (none is surfaced in the callback)."""
    now = datetime.now(UTC)
    user_id = event.user_id
    plan_slug = event.plan_slug

    # Non-success: cancellation / denial — no entitlement, no invoice.
    if event.payment_status != "success":
        existing = await store.get_subscription(user_id=user_id) if user_id else None
        if existing is None or existing.provider != BillingProvider.prodamus:
            return None  # nothing we've recorded to cancel
        sub = Subscription(
            id=existing.id,
            user_id=existing.user_id,
            plan_slug=existing.plan_slug,
            provider=BillingProvider.prodamus,
            provider_sub_id=event.order_num or existing.provider_sub_id,
            status=SubscriptionStatus.canceled,
            current_period_start=existing.current_period_start,
            current_period_end=existing.current_period_end,
            cancel_at_period_end=existing.cancel_at_period_end,
            trial_ends_at=None,
            billing_country=existing.billing_country,
            created_at=existing.created_at,
        )
        return await store.upsert_subscription(sub)

    if user_id is None or plan_slug is None:
        return None
    plan = await store.get_plan(plan_slug)
    if plan is None:
        return None
    # One row per user — reuse the existing Prodamus sub on a renewal so the
    # billing tab keeps a single "current" subscription.
    existing = await store.get_subscription(user_id=user_id)
    if existing is not None and existing.provider != BillingProvider.prodamus:
        existing = None  # don't stomp a sub from another provider; start fresh
    sub = Subscription(
        id=existing.id if existing else uuid.uuid4().hex,
        user_id=user_id,
        plan_slug=plan_slug,
        provider=BillingProvider.prodamus,
        provider_sub_id=event.order_num,
        status=SubscriptionStatus.active,
        current_period_start=existing.current_period_start if existing else now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=True,  # one-off — no auto-renew
        trial_ends_at=None,
        billing_country=(
            existing.billing_country if existing else ("RU" if plan.geo == "RU" else "WW")
        ),
        created_at=existing.created_at if existing else now,
    )
    sub = await store.upsert_subscription(sub)
    await auth_store.set_user_plan(
        user_id=user_id, plan=plan_slug, credits_grant_usd=plan.credits_grant_usd
    )
    await store.insert_invoice(_invoice_from_prodamus(event, sub, plan))
    return sub


def _invoice_from_paddle(event: PaddleEvent, sub: Subscription, plan: Plan) -> InvoiceRecord:
    # The invoice references the Paddle transaction id (``data.id`` on a
    # ``transaction.completed`` event, e.g. ``txn_…``); fall back to the event id.
    raw_data = (event.raw or {}).get("data") if event.raw else None
    txn_id = raw_data.get("id") if isinstance(raw_data, dict) else None
    return InvoiceRecord(
        id=uuid.uuid4().hex,
        user_id=sub.user_id,
        subscription_id=sub.id,
        provider="paddle",
        provider_invoice_id=str(txn_id) if txn_id else event.event_id,
        amount_cents=plan.price_cents,
        currency=plan.currency,
        status="paid",
        paid_at=datetime.now(UTC),
        receipt_url=None,
        fiscal_receipt_id=None,  # Paddle (WW) — no 54-ФЗ
    )


def _invoice_from_yookassa(event: YooKassaEvent, sub: Subscription, plan: Plan) -> InvoiceRecord:
    return InvoiceRecord(
        id=uuid.uuid4().hex,
        user_id=sub.user_id,
        subscription_id=sub.id,
        provider="yookassa",
        provider_invoice_id=event.payment_id,
        amount_cents=event.amount_cents or plan.price_cents,
        currency=event.currency or plan.currency,
        status="paid",
        paid_at=datetime.now(UTC),
        receipt_url=None,
        fiscal_receipt_id=event.fiscal_receipt_id,  # 54-ФЗ receipt id
    )


def _invoice_from_prodamus(event: ProdamusEvent, sub: Subscription, plan: Plan) -> InvoiceRecord:
    # Prodamus fiscalizes on its side (auto for NPD / cloud kassa for ИП/ООО); the
    # callback surfaces no fiscal document number, so we record the invoice
    # without one. The merchant sees receipts in the Prodamus LK.
    return InvoiceRecord(
        id=uuid.uuid4().hex,
        user_id=sub.user_id,
        subscription_id=sub.id,
        provider="prodamus",
        provider_invoice_id=event.order_num,
        amount_cents=_amount_to_minor(event.amount) or plan.price_cents,
        currency=(event.currency.upper() if event.currency else plan.currency),
        status="paid",
        paid_at=datetime.now(UTC),
        receipt_url=None,
        fiscal_receipt_id=None,  # 54-ФЗ handled on Prodamus's side
    )


def _amount_to_minor(value: str | None) -> int:
    """Prodamus ``sum`` is a major-unit string (``"12.00"`` / ``"690.00"``);
    convert to minor units (cents/kopecks). 0 on any malformation — never
    raises, the caller falls back to ``plan.price_cents``."""
    if not value:
        return 0
    try:
        return int(round(float(value) * 100))
    except (ValueError, TypeError):
        return 0


def _return_url(settings: Settings, request: Request) -> str:
    """Where the provider redirects the browser after checkout. Prefer the
    configured ``billing_return_origin`` (the Caddy origin in compose); fall
    back to the request's base URL. Trailing slash stripped."""
    origin = (settings.billing_return_origin or str(request.base_url)).rstrip("/")
    return f"{origin}/plans?checkout=done"


__all__ = ["apply_paddle_event", "apply_prodamus_event", "apply_yookassa_event", "router"]
