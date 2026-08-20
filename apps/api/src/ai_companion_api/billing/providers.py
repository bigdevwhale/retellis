"""Provider clients + webhook verification (Paddle WW, ЮKassa RU).

The purchase is a redirect to the provider's hosted checkout — no card data is
collected on our side (PCI-scope SAQ-A). Webhooks are the single source of
truth for subscription state.

Signature verification is implemented locally and is the ONLY auth on the
webhook routes (they're in ``_PUBLIC_POST``). Outbound checkout/portal calls
hit the provider API; they 503 when the provider isn't configured (the
bootstrap validator refuses to boot hosted+feature_billing with no provider
at all, but a single-provider deployment still 503s the other geo).

All provider secrets are env-only and never logged — ``observability/redaction``
scrubs ``paddle_``/``yukassa_`` token prefixes and card PANs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_companion_contracts import BillingProvider, CheckoutSession, PortalSession

from ..config import Settings

logger = logging.getLogger(__name__)

# Webhook timestamp skew window (replay protection). Paddle signs ``ts:body``;
# a notification older than this is rejected even if the signature matches.
_WEBHOOK_MAX_SKEW_SECONDS = 300


@dataclass
class PaddleEvent:
    """Normalised Paddle webhook event. Paddle Billing posts ``subscription.*``
    events; we care about a small subset. ``subscription_id`` is the provider
    subscription id we link back to a ``subscriptions`` row."""

    event_id: str  # Paddle's own event id — the idempotency key
    event_type: str  # e.g. "subscription.created", "transaction.completed", "subscription.canceled"
    subscription_id: str | None
    status: str | None  # Paddle subscription status: active, trialing, past_due, canceled, paused
    plan_id: str | None  # Paddle price/plan id — mapped to our plan_slug via passthrough
    current_period_end: datetime | None
    cancel_at_period_end: bool = False
    customer_id: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class YooKassaEvent:
    """Normalised ЮKassa notification. ЮKassa posts ``payment.succeeded`` /
    ``payment.canceled`` / ``refund.succeeded``. We re-fetch the payment from
    their API before trusting the body (signature/IP alone aren't enough), so
    this is built from the AUTHORITATIVE GET /payments/{id} response, not the
    notification payload."""

    event_id: str  # ``"{payment_id}:{event}"`` — our idempotency key
    event_type: str  # "payment.succeeded" | "payment.canceled" | "payment.waiting_for_capture"
    payment_id: str
    status: str  # succeeded | canceled | pending
    amount_cents: int
    currency: str
    recurring_payment_id: str | None  # the recurring token for autorenew (our provider_sub_id)
    fiscal_receipt_id: str | None  # 54-ФЗ receipt id
    metadata: dict[str, Any]
    raw: dict[str, Any] | None = None


# --- Paddle: signature verification + event parsing --------------------------


def paddle_verify_signature(
    raw_body: bytes,
    signature_header: str,
    secret: str,
    *,
    now: datetime | None = None,
    max_skew_seconds: int = _WEBHOOK_MAX_SKEW_SECONDS,
) -> bool:
    """Verify a Paddle webhook signature. Paddle signs ``ts:body`` with
    HMAC-SHA256 and sends ``Paddle-Signature: ts=<unix>;h1=<hex>``. We
    recompute h1 and compare in constant time, and reject a stale ``ts``
    (replay protection). Returns False on any malformation — the caller 401s."""
    if not secret or not signature_header:
        return False
    parts: dict[str, str] = {}
    for chunk in signature_header.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip()] = v.strip()
    ts = parts.get("ts")
    h1 = parts.get("h1")
    if not ts or not h1:
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now = now or datetime.now(UTC)
    if abs((now - datetime.fromtimestamp(ts_int, UTC)).total_seconds()) > max_skew_seconds:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{ts}:{raw_body.decode('utf-8', errors='replace')}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, h1)


def paddle_parse_event(payload: dict[str, Any]) -> PaddleEvent:
    """Parse a Paddle Billing webhook payload into a normalised event.

    Paddle Billing puts the entity snapshot directly at ``data`` — there is no
    ``data.subscription`` nesting (that was Paddle Classic's shape). For
    ``subscription.*`` events ``data`` IS the subscription (``data.id`` is the
    sub id, ``data.status``, ``data.custom_data``, ``data.items``); for
    ``transaction.*`` events ``data`` is the transaction and carries a
    ``subscription_id`` reference plus its own ``custom_data``. We pull the
    fields we need and leave the rest in ``raw`` for diagnostics.
    """
    data = payload.get("data") or {}
    event_type = str(payload.get("event_type") or "")

    if event_type.startswith("subscription."):
        # ``data`` is the subscription entity.
        sub = data if isinstance(data, dict) else {}
        items = sub.get("items") or []
        plan_id = None
        if items:
            price = items[0].get("price") or {}
            plan_id = price.get("id")
        cbp = sub.get("current_billing_period") or {}
        period_end = _parse_iso(cbp.get("ends_at")) if cbp.get("ends_at") else None
        sched = sub.get("scheduled_change")
        cancel_at_period_end = isinstance(sched, dict) and sched.get("type") == "cancel"
        subscription_id = sub.get("id")
        status = sub.get("status")
        customer_id = sub.get("customer_id") if isinstance(sub.get("customer_id"), str) else None
    elif event_type.startswith("transaction."):
        # ``data`` is the transaction; the subscription is a reference. The
        # transaction carries the passthrough ``custom_data`` Paddle propagated
        # from checkout, and ``subscription_id`` links it to a subscription row.
        subscription_id = data.get("subscription_id") if isinstance(data, dict) else None
        status = None
        plan_id = None
        period_end = None
        cancel_at_period_end = False
        customer_id = data.get("customer_id") if isinstance(data.get("customer_id"), str) else None
    else:
        subscription_id = None
        status = None
        plan_id = None
        period_end = None
        cancel_at_period_end = False
        customer_id = None

    return PaddleEvent(
        event_id=str(payload.get("event_id") or ""),
        event_type=event_type,
        subscription_id=subscription_id,
        status=status,
        plan_id=plan_id,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
        customer_id=customer_id,
        raw=payload,
    )


# --- ЮKassa: re-fetch verification + notification parsing --------------------


def yukassa_parse_notification(payload: dict[str, Any]) -> YooKassaEvent | None:
    """Parse a ЮKassa notification into a *pre-verification* event. The status
    here is NOT trusted — the caller must re-fetch the payment via
    ``yukassa_fetch_payment`` before acting. Returns None for events we don't
    handle (refunds, payment.waiting_for_capture for non-autocapture)."""
    event = payload.get("event")
    obj = payload.get("object") or {}
    payment_id = obj.get("id")
    if not event or not payment_id:
        return None
    event_type = str(event)
    # We only act on terminal / capture-ready payment events. waiting_for_capture
    # is handled for auto-capture flows (we capture immediately).
    if event_type not in (
        "payment.succeeded",
        "payment.canceled",
        "payment.waiting_for_capture",
    ):
        return None
    amount = obj.get("amount") or {}
    metadata = obj.get("metadata") or {}
    # The recurring token (for autorenew) arrives in payment_method.recursive or
    # in the captured payment's ``payment_method``; we read it defensively.
    pm = obj.get("payment_method") or {}
    recurring = pm.get("id") if isinstance(pm, dict) else None
    receipt = obj.get("receipt_registration") or None
    return YooKassaEvent(
        event_id=f"{payment_id}:{event_type}",
        event_type=event_type,
        payment_id=str(payment_id),
        status=str(obj.get("status") or ""),
        amount_cents=_rub_to_minor(amount.get("value")),
        currency=str(amount.get("currency") or "RUB"),
        recurring_payment_id=recurring,
        fiscal_receipt_id=str(receipt) if receipt else None,
        metadata=metadata if isinstance(metadata, dict) else {},
        raw=payload,
    )


async def yukassa_fetch_payment(
    payment_id: str, settings: Settings, *, fetcher=None
) -> dict[str, Any] | None:
    """Re-fetch a payment from the ЮKassa API (HTTP Basic auth = shopId:secret).
    This is the AUTHORITATIVE state — the notification body is not trusted
    alone. Returns the payment object or None on any failure (caller treats as
    "skip, 200" — we never grant credits on an unverified payment). ``fetcher``
    is injectable for tests (real path uses httpx)."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        return None
    if fetcher is not None:
        return await fetcher(payment_id, settings)
    import httpx  # lazy: keep zero-config import path clean

    basic = base64.b64encode(
        f"{settings.yukassa_shop_id}:{settings.yukassa_secret_key}".encode()
    ).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://api.yookassa.ru/v3/payments/{payment_id}",
                headers={"Authorization": f"Basic {basic}", "Idempotence-Key": payment_id},
            )
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as exc:  # network / DNS / timeout — never crash the webhook
        logger.warning("yookassa payment re-fetch failed: %s: %s", type(exc).__name__, exc)
        return None


def yukassa_event_from_payment(
    payment: dict[str, Any], event_type: str, *, fiscal_receipt_id: str | None = None
) -> YooKassaEvent:
    """Build the AUTHORITATIVE event from a re-fetched payment object. The
    status here is trusted (it came from our own GET).

    ``fiscal_receipt_id`` is NOT read from the payment object — the payment only
    carries ``receipt_registration`` (a status: succeeded/pending/canceled), not
    the 54-ФЗ fiscal document number. That lives on the receipt object, fetched
    separately by ``yukassa_fetch_receipt`` and passed in here. ``recurring_payment_id``
    is the saved payment-method token — only set when ``payment_method.saved`` is
    true (i.e. the user opted in / we forced ``save_payment_method`` at checkout);
    a one-off payment has no recurring token and the caller falls back to the
    payment id."""
    amount = payment.get("amount") or {}
    pm = payment.get("payment_method") or {}
    recurring = pm.get("id") if isinstance(pm, dict) and pm.get("saved") else None
    return YooKassaEvent(
        event_id=f"{payment.get('id')}:{event_type}",
        event_type=event_type,
        payment_id=str(payment.get("id") or ""),
        status=str(payment.get("status") or ""),
        amount_cents=_rub_to_minor(amount.get("value")),
        currency=str(amount.get("currency") or "RUB"),
        recurring_payment_id=recurring,
        fiscal_receipt_id=fiscal_receipt_id,
        metadata=payment.get("metadata") or {},
        raw=payment,
    )


async def yukassa_fetch_receipt(
    payment_id: str, settings: Settings, *, fetcher=None
) -> dict[str, Any] | None:
    """Fetch the 54-ФЗ receipt for a payment from the ЮKassa API. The payment
    object only carries ``receipt_registration`` (a status); the fiscal document
    number / attribute live on the receipt object, retrieved via
    ``GET /v3/receipts?payment_id=``. Returns the first registered (``succeeded``)
    receipt with a fiscal document number, or None on any failure / when the
    receipt isn't registered yet (a ``pending`` registration at webhook time is
    normal — the fiscal id can be backfilled later). ``fetcher`` is injectable
    for tests; the real path uses httpx. Best-effort: a None return never blocks
    the webhook — the invoice is recorded without a fiscal id."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        return None
    if fetcher is not None:
        return await fetcher(payment_id, settings)
    import httpx  # lazy: keep zero-config import path clean

    basic = base64.b64encode(
        f"{settings.yukassa_shop_id}:{settings.yukassa_secret_key}".encode()
    ).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.yookassa.ru/v3/receipts",
                params={"payment_id": payment_id},
                headers={"Authorization": f"Basic {basic}"},
            )
            if r.status_code != 200:
                return None
            for rec in r.json().get("items") or []:
                if rec.get("status") == "succeeded" and rec.get("fiscal_document_number"):
                    return rec
            return None
    except Exception as exc:  # network / DNS / timeout — never crash the webhook
        logger.warning("yookassa receipt fetch failed: %s: %s", type(exc).__name__, exc)
        return None


def fiscal_id_from_receipt(receipt: dict[str, Any] | None) -> str | None:
    """Extract the 54-ФЗ fiscal receipt id from a receipt object. The receipt is
    identified by ``fiscal_document_number`` (the human-facing document number);
    ``fiscal_attribute`` is the FFD fiscal signature. We store the document
    number as the invoice's ``fiscal_receipt_id``. None when the receipt is
    absent or not yet registered."""
    if not receipt:
        return None
    fdn = receipt.get("fiscal_document_number")
    return str(fdn) if fdn else None


# --- Outbound: checkout + portal (hit the provider API) ---------------------


async def paddle_create_checkout(
    *,
    plan,
    user_id: str,
    return_url: str,
    settings: Settings,
    poster=None,
) -> CheckoutSession:
    """Create a Paddle hosted-checkout transaction. ``POST /transactions`` with
    the plan's ``price_id`` + passthrough ``custom_data`` (so the webhook links
    the payment back WITHOUT trusting the checkout redirect); Paddle returns
    ``data.checkout.url`` — the hosted payment link the browser is redirected to
    (card data is collected on Paddle's domain, PCI-scope SAQ-A).

    The transactions API has NO ``return_url`` field — the after-payment success
    redirect is configured on Paddle's default payment link in the dashboard (an
    operator step, exactly like linking the price). ``return_url`` here is only a
    fallback when Paddle doesn't return a checkout url. 503s when Paddle isn't
    configured (no API key) or the plan isn't linked yet (no ``provider_price_id``
    — an operator step until prices are created in the Paddle dashboard).

    ``poster`` is injectable for tests (real path uses httpx); it receives the
    URL + request body + settings and returns the parsed ``data`` object."""
    if not settings.paddle_api_key or not plan.provider_price_id:
        raise _ProviderUnavailable("paddle")
    base = (
        "https://api.paddle.com"
        if settings.paddle_environment == "production"
        else "https://sandbox-api.paddle.com"
    )
    body = {
        "items": [{"price_id": plan.provider_price_id, "quantity": 1}],
        "custom_data": {"user_id": user_id, "plan_slug": plan.slug},
    }
    if poster is not None:
        data = await poster(f"{base}/transactions", body, settings)
    else:
        import httpx  # lazy: keep zero-config import path clean

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{base}/transactions",
                headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
                json=body,
            )
            if r.status_code >= 400:
                raise _ProviderUnavailable("paddle")
            data = r.json().get("data") or {}
    checkout_url = (data.get("checkout") or {}).get("url") if isinstance(data, dict) else None
    return CheckoutSession(
        redirect_url=checkout_url or return_url,
        provider=BillingProvider.paddle,
        provider_sub_id=data.get("id") if isinstance(data, dict) else None,  # txn_…
    )


async def paddle_create_portal(
    *, user_id: str, settings: Settings, billing_store, poster=None
) -> PortalSession:
    """Open Paddle's customer portal for the user. ``POST
    /customers/{customer_id}/portal-sessions`` returns authenticated deep links
    (cancel / update payment method / overview) — the recommended self-service
    flow (no email sign-in wall, unlike the bare ``management_urls`` from
    ``GET /subscriptions/{id}``). Requires the ``customer_id`` we persisted from
    webhooks onto the billing profile (``set_provider_customer_id``); 503s when
    Paddle isn't configured or we have no customer id yet (the user hasn't paid,
    so there's nothing to manage).

    ``poster`` is injectable for tests (real path uses httpx); it receives the
    URL + settings and returns the parsed ``data`` object."""
    if not settings.paddle_api_key:
        raise _ProviderUnavailable("paddle")
    if billing_store is None:
        raise _ProviderUnavailable("paddle")
    profile = await billing_store.get_billing_profile(user_id=user_id)
    customer_id = profile.provider_customer_id if profile is not None else None
    if not customer_id:
        raise _ProviderUnavailable("paddle")
    base = (
        "https://api.paddle.com"
        if settings.paddle_environment == "production"
        else "https://sandbox-api.paddle.com"
    )
    if poster is not None:
        data = await poster(f"{base}/customers/{customer_id}/portal-sessions", settings)
    else:
        import httpx  # lazy: keep zero-config import path clean

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{base}/customers/{customer_id}/portal-sessions",
                headers={"Authorization": f"Bearer {settings.paddle_api_key}"},
                json={},
            )
            if r.status_code >= 400:
                raise _ProviderUnavailable("paddle")
            data = r.json().get("data") or {}
    general = ((data.get("urls") or {}).get("general") or {}) if isinstance(data, dict) else {}
    redirect = general.get("overview")
    if not redirect:
        raise _ProviderUnavailable("paddle")
    return PortalSession(redirect_url=redirect)


async def yukassa_create_checkout(
    *, plan, user_id: str, return_url: str, settings: Settings
) -> CheckoutSession:
    """Create a ЮKassa payment (redirect to ЮKassa's hosted checkout). 54-ФЗ
    receipt data is embedded (tax_system_code / product_type=service /
    payment_method_type=full_payment) so the online-kassa cheque is filed.
    ``save_payment_method=True`` saves the user's payment method so the
    recurring token (``payment_method.id`` with ``saved=true``) comes back on
    ``payment.succeeded`` — without it the "subscription" is a one-off payment
    that never auto-renews."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        raise _ProviderUnavailable("yookassa")
    import httpx  # lazy

    basic = base64.b64encode(
        f"{settings.yukassa_shop_id}:{settings.yukassa_secret_key}".encode()
    ).decode("ascii")
    # Idempotency key with entropy: two checkouts for the same user+plan within
    # the same second must NOT collide (ЮKassa would return the first payment).
    idem = f"checkout-{user_id}-{plan.slug}-{secrets.token_hex(8)}"
    amount_value = f"{plan.price_cents / 100:.2f}"
    body = {
        "amount": {"value": amount_value, "currency": plan.currency},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "save_payment_method": True,  # save the method → recurring token for autorenew
        "metadata": {"user_id": user_id, "plan_slug": plan.slug},
        "description": f"Retellis {plan.name} ({plan.slug})",
        "receipt": {
            "customer": {},
            "items": [
                {
                    "description": f"Retellis {plan.name} subscription",
                    "quantity": "1",
                    "amount": {"value": amount_value, "currency": plan.currency},
                    "vat_code": 1,
                    "payment_method_type": "full_payment",
                    "product_type": "service",
                }
            ],
            "tax_system_code": 2,  # simplified income (УСН доход) — operator-configurable
        },
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            "https://api.yookassa.ru/v3/payments",
            headers={"Authorization": f"Basic {basic}", "Idempotence-Key": idem},
            json=body,
        )
        if r.status_code >= 400:
            raise _ProviderUnavailable("yookassa")
        data = r.json()
    confirmation = data.get("confirmation") or {}
    return CheckoutSession(
        redirect_url=confirmation.get("confirmation_url") or return_url,
        provider=BillingProvider.yookassa,
        provider_sub_id=data.get("id"),
    )


async def yukassa_create_portal(*, user_id: str, settings: Settings) -> PortalSession:
    if not settings.yukassa_shop_id:
        raise _ProviderUnavailable("yookassa")
    # ЮKassa has no self-service portal; recurring autorenew is managed via the
    # payment method. Honest: we send the user to the app's billing tab which
    # shows the recurring payment + a cancel action (the cancel hits the ЮKassa
    # API to revoke the recurring token). For now return the merchant site.
    return PortalSession(redirect_url="https://yookassa.ru/my")


# --- Prodamus: RU acquirer (самозанятый/ИП-НПД/ИП/ООО), RU + foreign cards ----


@dataclass
class ProdamusEvent:
    """Normalised Prodamus webhook event. Prodamus posts a JSON callback (when
    ``callbackType=json`` is set at checkout) with a ``submit`` sub-object the
    ``Sign`` header is computed over, plus the business fields at top level.

    ``order_id`` is Prodamus's INTERNAL order id (the idempotency key);
    ``order_num`` is OUR internal order id (we passed it as ``order_id`` at
    checkout — Prodamus renames it on the way back) and carries the
    ``user_id`` + ``plan_slug`` linkage we encoded into it. ``payment_status``
    is the terminal signal: ``success`` grants, ``order_canceled``/``order_denied``
    do not."""

    event_id: str  # Prodamus internal order_id — idempotency key
    order_num: str  # our internal order id — carries user_id + plan_slug
    payment_status: str  # success | order_canceled | order_denied
    amount: str | None
    currency: str | None
    user_id: str | None  # parsed from order_num
    plan_slug: str | None  # parsed from order_num
    raw: dict[str, Any] | None = None


def _prodamus_canonical(data: Any, *, ensure_ascii: bool = True) -> str:
    """Prodamus HMAC canonical form (matches ``Hmac.php``):

    1. cast every value to a string (PHP ``(string)`` semantics: ``true``→``"1"``,
       ``false``→``""``, ``null``→``""``);
    2. sort keys alphabetically, recursively (nested dicts included; arrays
       keep their order — they're positional, not keyed);
    3. JSON-encode compact (``separators=(",", ":")``);
    4. escape forward slashes (``/`` → ``\\/``) — PHP ``json_encode`` does this
       by default, and Prodamus's algorithm calls it out explicitly.

    ``ensure_ascii`` toggles PHP's ``JSON_UNESCAPED_UNICODE`` ambiguity: default
    ``json_encode`` ASCII-escapes non-ASCII (``ensure_ascii=True``); some Prodamus
    integrations pass ``JSON_UNESCAPED_UNICODE`` (``False``). Checkout payloads
    are ASCII-only (plan names are Latin), so create uses the default; webhook
    verify accepts EITHER to be robust to Cyrillic in ``submit`` without
    weakening security (both HMACs are keyed by the secret)."""

    def stringize(v: Any) -> str:
        if isinstance(v, bool):
            return "1" if v else ""
        if v is None:
            return ""
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else repr(v)
        return str(v)

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            return {str(k): walk(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [walk(v) for v in o]
        return stringize(o)

    canon = walk(data)
    s = json.dumps(canon, separators=(",", ":"), sort_keys=True, ensure_ascii=ensure_ascii)
    return s.replace("/", "\\/")


def _prodamus_sign(data: dict[str, Any], secret: str, *, ensure_ascii: bool = True) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _prodamus_canonical(data, ensure_ascii=ensure_ascii).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def prodamus_verify_signature(payload: dict[str, Any], sign_header: str, secret: str) -> bool:
    """Verify a Prodamus webhook ``Sign`` header. With ``callbackType=json`` the
    signature is computed over the ``submit`` sub-object (the same fields,
    isolated for verification), NOT the whole body. Returns False on any
    malformation — the caller 401s. Accepts either unicode-escaped or raw
    canonicalization (see ``_prodamus_canonical``); constant-time compare."""
    if not secret or not sign_header:
        return False
    submit = payload.get("submit") if isinstance(payload, dict) else None
    if not isinstance(submit, dict):
        return False
    sign = sign_header.strip()
    for ensure_ascii in (True, False):
        if hmac.compare_digest(_prodamus_sign(submit, secret, ensure_ascii=ensure_ascii), sign):
            return True
    return False


def prodamus_parse_event(payload: dict[str, Any]) -> ProdamusEvent | None:
    """Parse a Prodamus JSON webhook into a normalised event. ``user_id`` +
    ``plan_slug`` are recovered from ``order_num`` (we encoded them at checkout
    as ``retellis-<user_id>-<plan_slug>-<nonce>``); the passthrough is the
    linkage — the webhook body is NOT trusted alone (the ``Sign`` is the auth).
    Returns None when ``order_num`` is missing or doesn't decode (an event we
    can't link to a user/plan — the handler acks and ignores)."""
    order_id = str(payload.get("order_id") or "")
    order_num = str(payload.get("order_num") or "")
    status = str(payload.get("payment_status") or "")
    # ``order_num`` is ``retellis:<user_id>:<plan_slug>:<nonce>`` (see
    # ``prodamus_create_checkout`` for the separator choice). A shape that
    # doesn't decode → no linkage → the handler acks and ignores.
    parts = order_num.split(":") if order_num else []
    user_id = parts[1] if len(parts) == 4 and parts[0] == "retellis" else None
    plan_slug = parts[2] if len(parts) == 4 and parts[0] == "retellis" else None
    return ProdamusEvent(
        event_id=order_id,
        order_num=order_num,
        payment_status=status,
        amount=str(payload.get("sum")) if payload.get("sum") is not None else None,
        currency=str(payload.get("currency") or "").lower() or None,
        user_id=user_id,
        plan_slug=plan_slug,
        raw=payload,
    )


def _prodamus_flatten(obj: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict/list into Prodamus's bracket form
    (``products[0][name]``). Prodamus parses this back into the nested ``$_POST``
    it signs, so the round-trip must reproduce the original structure."""
    items: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}[{k}]" if prefix else str(k)
            items.update(_prodamus_flatten(v, key))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            items.update(_prodamus_flatten(v, f"{prefix}[{i}]"))
    else:
        items[prefix] = str(obj) if not isinstance(obj, str) else obj
    return items


async def prodamus_create_checkout(
    *,
    plan,
    user_id: str,
    return_url: str,
    settings: Settings,
    poster=None,
) -> CheckoutSession:
    """Create a Prodamus payment link. ``POST`` to the merchant's payform URL
    with ``do=link`` + a signed (HMAC-SHA256) nested payload; Prodamus returns a
    plain-text payment URL the browser is redirected to (card data collected on
    Prodamus's domain — PCI-scope SAQ-A). The link is a ONE-OFF payment (no
    ``subscription`` param): Prodamus auto-recurring needs the "Clubs" system
    (a subscription id pre-created in the merchant LK, +1% from the 2nd charge,
    1000₽ setup) — an operator opt-in we don't assume. Retellis's
    credit-metered model absorbs this: a successful payment grants the plan +
    credits for a 30-day window; the user re-checks-out to renew.

    ``order_id`` carries ``retellis-<user_id>-<plan_slug>-<nonce>`` so the
    webhook links the payment back WITHOUT trusting the redirect.
    ``npd_income_type=FROM_INDIVIDUAL`` is the самозанятый/NPD default. 54-ФЗ
    fiscalization runs on Prodamus's side (auto for NPD); we don't assert FFD
    tax/payment codes we can't guarantee — the merchant sets defaults in the LK.
    503s when Prodamus isn't configured (no secret / payform URL / sys).

    ``poster`` is injectable for tests (real path uses httpx); it receives the
    payform URL + flat form dict + settings and returns the response text."""
    if (
        not settings.prodamus_secret_key
        or not settings.prodamus_payform_url
        or not settings.prodamus_sys
    ):
        raise _ProviderUnavailable("prodamus")
    nonce = secrets.token_hex(8)
    # `:` separates the encoded fields — a Retellis user_id is a dashed UUID
    # (``237020e9-...``) and plan_slug uses ``_``, so ``:`` is the one char that
    # can't appear in any component. The webhook parses this back to link the
    # payment to the user + plan WITHOUT trusting the redirect.
    order_num = f"retellis:{user_id}:{plan.slug}:{nonce}"
    # Price: plan.price_cents is minor units (cents/kopecks); Prodamus takes
    # major-unit amounts (12.00, not 1200).
    price = f"{plan.price_cents / 100:.2f}"
    params: dict[str, Any] = {
        "do": "link",
        "sys": settings.prodamus_sys,
        "currency": _prodamus_currency(plan.currency),
        "order_id": order_num,
        "products": [
            {"name": f"Retellis {plan.name} ({plan.slug})", "price": price, "quantity": "1"}
        ],
        "urlSuccess": return_url,
        "urlReturn": return_url,
        "urlNotification": _prodamus_webhook_url(settings, return_url),
        "callbackType": "json",  # webhook comes as JSON with a `submit` for verification
        "npd_income_type": "FROM_INDIVIDUAL",  # самозанятый/NPD default
    }
    params["signature"] = _prodamus_sign(params, settings.prodamus_secret_key)
    flat = _prodamus_flatten(params)
    if poster is not None:
        text = await poster(settings.prodamus_payform_url, flat, settings)
    else:
        import httpx  # lazy: keep zero-config import path clean

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(settings.prodamus_payform_url, data=flat)
            if r.status_code >= 400:
                raise _ProviderUnavailable("prodamus")
            text = r.text
    redirect = text.strip() if isinstance(text, str) else ""
    # do=link returns a plain-text shortened payment URL; a non-URL body means
    # Prodamus rejected the request (bad signature / sys / etc.) → honest 503.
    if not redirect.startswith("http"):
        raise _ProviderUnavailable("prodamus")
    return CheckoutSession(
        redirect_url=redirect,
        provider=BillingProvider.prodamus,
        provider_sub_id=order_num,  # our internal order id (echoed as order_num)
    )


async def prodamus_create_portal(*, user_id: str, settings: Settings) -> PortalSession:
    """Prodamus has no merchant-callable self-service portal API. For one-off
    payments there's nothing to auto-cancel (no recurring token to revoke), so
    a portal redirect would claim management we can't perform — "disclose, don't
    perform". 503; the billing tab surfaces the subscription + its expiry
    instead. (Prodamus "Clubs" recurring, if the operator later enables it, is
    managed in the Prodamus merchant LK.)"""
    raise _ProviderUnavailable("prodamus")


class _ProviderUnavailable(Exception):
    """Raised when a provider isn't configured (no creds / no price link). The
    router maps this to 503 so the UI can surface "checkout unavailable"."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"provider {name} not configured")


# --- helpers -----------------------------------------------------------------


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _rub_to_minor(value: Any) -> int:
    """ЮKassa amounts are strings like "690.00"; convert to kopecks (minor
    units). Returns 0 on any malformation — never raises."""
    if value is None:
        return 0
    try:
        return int(round(float(value) * 100))
    except (ValueError, TypeError):
        return 0


def _prodamus_currency(currency: str) -> str:
    """Prodamus takes lowercase currency codes (``rub``/``usd``/``eur``/``kzt``)."""
    return (currency or "").strip().lower()


def _prodamus_webhook_url(settings: Settings, return_url: str) -> str:
    """The Prodamus ``urlNotification`` target. Prefer the configured
    ``billing_return_origin`` (the Caddy origin in compose); fall back to the
    origin implied by the ``return_url`` the router built
    (``{origin}/plans?checkout=done``). Trailing slash stripped."""
    origin = (settings.billing_return_origin or "").rstrip("/")
    if not origin:
        origin = return_url.split("/plans?", 1)[0].rstrip("/")
    return f"{origin}/v1/billing/webhook/prodamus"


__all__ = [
    "PaddleEvent",
    "ProdamusEvent",
    "YooKassaEvent",
    "fiscal_id_from_receipt",
    "paddle_create_checkout",
    "paddle_create_portal",
    "paddle_parse_event",
    "paddle_verify_signature",
    "prodamus_create_checkout",
    "prodamus_create_portal",
    "prodamus_parse_event",
    "prodamus_verify_signature",
    "yukassa_create_checkout",
    "yukassa_create_portal",
    "yukassa_event_from_payment",
    "yukassa_fetch_payment",
    "yukassa_fetch_receipt",
    "yukassa_parse_notification",
]
