"""Billing persistence — plans, subscriptions, invoices, webhook idempotency.

Mirrors ``memory/store.py`` / ``auth/store.py`` / ``family/store.py``: one
``BillingStore`` Protocol with an in-memory and a Postgres implementation,
picked by ``make_billing_store(settings)``. The in-memory store is the
zero-config default (tests, local, graceful fallback when the DB is
unreachable); the Postgres store uses the shared async session factory from
``db.session``.

The webhook handler is the single source of truth for subscription state —
the checkout callback redirect does NOT mutate state. A successful payment
calls ``AuthStore.set_user_plan`` (atomic plan + additive credit top-up);
this store records the lifecycle (subscription row, invoice row, webhook
idempotency guard) but does NOT touch ``users`` directly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ai_companion_contracts import (
    BillingProvider,
    Plan,
    PlanGeo,
    PlanInterval,
    Subscription,
    SubscriptionStatus,
)

from ..config import Settings

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    return uuid.uuid4().hex


# Seeded by migration 0017. Kept here too so the in-memory store (tests,
# zero-config local) serves the same catalogue without a DB. Prices are minor
# units (cents for USD, kopecks for RUB); credits_grant_usd is USD-denominated.
# plus→$10, pro→$25; Paddle 7-day trial (WW only — РФ has no trial due to
# 54-ФЗ); RUB price list for RU (690₽/1390₽).
SEED_PLANS: list[Plan] = [
    Plan(
        slug="plus_ww",
        name="Plus",
        price_cents=1200,
        currency="USD",
        interval=PlanInterval.month,
        geo=PlanGeo.WW,
        trial_days=7,
        credits_grant_usd=10.0,
        active=True,
    ),
    Plan(
        slug="pro_ww",
        name="Pro",
        price_cents=2400,
        currency="USD",
        interval=PlanInterval.month,
        geo=PlanGeo.WW,
        trial_days=7,
        credits_grant_usd=25.0,
        active=True,
    ),
    Plan(
        slug="plus_ru",
        name="Plus",
        price_cents=69000,
        currency="RUB",
        interval=PlanInterval.month,
        geo=PlanGeo.RU,
        trial_days=0,
        credits_grant_usd=10.0,
        active=True,
    ),
    Plan(
        slug="pro_ru",
        name="Pro",
        price_cents=139000,
        currency="RUB",
        interval=PlanInterval.month,
        geo=PlanGeo.RU,
        trial_days=0,
        credits_grant_usd=25.0,
        active=True,
    ),
]


@dataclass
class InvoiceRecord:
    """A provider invoice / payment record. ``fiscal_receipt_id`` carries the
    54-ФЗ online-kassa receipt id for РФ (ЮKassa). Internal shape — the wire
    view is the billing tab's invoice list, rendered from this."""

    id: str
    user_id: str
    subscription_id: str | None
    provider: str
    provider_invoice_id: str
    amount_cents: int
    currency: str
    status: str = "open"
    paid_at: datetime | None = None
    receipt_url: str | None = None
    fiscal_receipt_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class BillingProfileRecord:
    """A user's billing profile — country + provider customer id. One row per
    user. ``country`` routes RU → ЮKassa, anything else → Paddle (NOT IP)."""

    user_id: str
    country: str
    provider: str | None = None
    provider_customer_id: str | None = None
    default_payment_method_token: str | None = None
    updated_at: datetime = field(default_factory=_utcnow)


@runtime_checkable
class BillingStore(Protocol):
    """Async billing store. All methods are awaitable, keyword-only."""

    async def table_exists(self) -> bool: ...
    async def get_plan(self, slug: str) -> Plan | None: ...
    async def list_plans(self, geo: str | None = None) -> list[Plan]: ...
    async def get_subscription(self, *, user_id: str) -> Subscription | None: ...
    async def get_subscription_by_provider_sub(
        self, *, provider: str, provider_sub_id: str
    ) -> Subscription | None: ...
    async def upsert_subscription(self, sub: Subscription) -> Subscription: ...
    async def list_all_subscriptions(self) -> list[Subscription]: ...
    async def last_payment_for(self, user_id: str) -> datetime | None: ...
    async def list_invoices(self, *, user_id: str) -> list[InvoiceRecord]: ...
    async def insert_invoice(self, inv: InvoiceRecord) -> InvoiceRecord: ...
    async def mark_webhook_processed(self, *, provider: str, event_id: str) -> bool: ...
    async def get_billing_profile(self, *, user_id: str) -> BillingProfileRecord | None: ...
    async def upsert_billing_profile(
        self,
        *,
        user_id: str,
        country: str,
        provider: str | None = None,
        provider_customer_id: str | None = None,
    ) -> BillingProfileRecord: ...

    async def set_provider_customer_id(
        self, *, user_id: str, provider: str, provider_customer_id: str
    ) -> None: ...


def _row_to_plan(row) -> Plan:  # type: ignore[no-untyped-def]
    return Plan(
        slug=row.slug,
        name=row.name,
        price_cents=row.price_cents,
        currency=row.currency,
        interval=PlanInterval(row.interval),
        geo=PlanGeo(row.geo),
        trial_days=row.trial_days,
        credits_grant_usd=row.credits_grant_usd,
        active=row.active,
        provider_price_id=row.provider_price_id,
    )


def _row_to_subscription(row) -> Subscription:  # type: ignore[no-untyped-def]
    return Subscription(
        id=row.id,
        user_id=row.user_id,
        plan_slug=row.plan_slug,
        provider=BillingProvider(row.provider),
        provider_sub_id=row.provider_sub_id,
        status=SubscriptionStatus(row.status),
        current_period_start=row.current_period_start,
        current_period_end=row.current_period_end,
        cancel_at_period_end=row.cancel_at_period_end,
        trial_ends_at=row.trial_ends_at,
        billing_country=row.billing_country,
        created_at=row.created_at,
    )


class InMemoryBillingStore:
    """Process-local billing store — zero-config default and test fixture.

    Plans are seeded from ``SEED_PLANS`` (the same catalogue migration 0017
    seeds into Postgres). Subscriptions/invoices/profiles live in process-local
    dicts. Webhook idempotency is a set of ``(provider, event_id)`` keys.
    """

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {p.slug: p for p in SEED_PLANS}
        self._subs_by_id: dict[str, Subscription] = {}
        self._subs_by_provider: dict[tuple[str, str], Subscription] = {}
        self._invoices: list[InvoiceRecord] = []
        self._webhook_events: set[tuple[str, str]] = set()
        self._profiles: dict[str, BillingProfileRecord] = {}

    async def table_exists(self) -> bool:
        return True

    async def get_plan(self, slug: str) -> Plan | None:
        return self._plans.get(slug)

    async def list_plans(self, geo: str | None = None) -> list[Plan]:
        plans = list(self._plans.values())
        if geo is not None:
            plans = [p for p in plans if p.geo == geo]
        return [p for p in plans if p.active]

    async def get_subscription(self, *, user_id: str) -> Subscription | None:
        # A user has at most one ACTIVE subscription; return the latest by
        # created_at to keep "current" meaningful after a cancel+re-subscribe.
        subs = [s for s in self._subs_by_id.values() if s.user_id == user_id]
        if not subs:
            return None
        subs.sort(key=lambda s: s.created_at)
        return subs[-1]

    async def get_subscription_by_provider_sub(
        self, *, provider: str, provider_sub_id: str
    ) -> Subscription | None:
        return self._subs_by_provider.get((provider, provider_sub_id))

    async def upsert_subscription(self, sub: Subscription) -> Subscription:
        self._subs_by_id[sub.id] = sub
        if sub.provider_sub_id is not None:
            self._subs_by_provider[(sub.provider, sub.provider_sub_id)] = sub
        return sub

    async def list_all_subscriptions(self) -> list[Subscription]:
        return list(self._subs_by_id.values())

    async def last_payment_for(self, user_id: str) -> datetime | None:
        user_invoices = [i for i in self._invoices if i.user_id == user_id and i.paid_at]
        if not user_invoices:
            return None
        return max(i.paid_at for i in user_invoices if i.paid_at)

    async def list_invoices(self, *, user_id: str) -> list[InvoiceRecord]:
        return [i for i in self._invoices if i.user_id == user_id]

    async def insert_invoice(self, inv: InvoiceRecord) -> InvoiceRecord:
        self._invoices.append(inv)
        return inv

    async def mark_webhook_processed(self, *, provider: str, event_id: str) -> bool:
        key = (provider, event_id)
        if key in self._webhook_events:
            return False
        self._webhook_events.add(key)
        return True

    async def get_billing_profile(self, *, user_id: str) -> BillingProfileRecord | None:
        return self._profiles.get(user_id)

    async def upsert_billing_profile(
        self,
        *,
        user_id: str,
        country: str,
        provider: str | None = None,
        provider_customer_id: str | None = None,
    ) -> BillingProfileRecord:
        existing = self._profiles.get(user_id)
        if existing is None:
            rec = BillingProfileRecord(
                user_id=user_id,
                country=country,
                provider=provider,
                provider_customer_id=provider_customer_id,
            )
            self._profiles[user_id] = rec
            return rec
        existing.country = country
        if provider is not None:
            existing.provider = provider
        if provider_customer_id is not None:
            existing.provider_customer_id = provider_customer_id
        existing.updated_at = _utcnow()
        return existing

    async def set_provider_customer_id(
        self, *, user_id: str, provider: str, provider_customer_id: str
    ) -> None:
        """Attach the provider's customer id to an existing billing profile. The
        customer id arrives in Paddle webhooks (``data.customer_id``) AFTER the
        checkout created the profile — we persist it so the customer portal can
        open an authenticated session. A no-op when no profile exists yet
        (checkout didn't run / subscription imported) — the next webhook that
        finds a profile will set it."""
        existing = self._profiles.get(user_id)
        if existing is None:
            return
        existing.provider = provider
        existing.provider_customer_id = provider_customer_id
        existing.updated_at = _utcnow()


class PostgresBillingStore:
    """SQLAlchemy billing store — used in ``docker compose`` (``COMPANION_USE_DB=1``).

    Shares the async engine from ``db.session`` with the other stores. Falls
    back to in-memory at the factory level if the billing tables are missing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _session(self):
        from ..db.session import get_sessionmaker  # lazy: keep zero-config import path clean

        sm = get_sessionmaker(self._settings)
        return sm()

    async def table_exists(self) -> bool:
        from sqlalchemy import select

        from ..db.models import BillingPlan as BillingPlanModel  # noqa: F401 (registry)

        async with await self._session() as s:
            r = await s.execute(select(BillingPlanModel.slug).limit(1))
            return r.first() is not None

    async def get_plan(self, slug: str) -> Plan | None:
        from sqlalchemy import select

        from ..db.models import BillingPlan as BillingPlanModel

        async with await self._session() as s:
            r = await s.execute(select(BillingPlanModel).where(BillingPlanModel.slug == slug))
            row = r.scalar_one_or_none()
            return _row_to_plan(row) if row is not None else None

    async def list_plans(self, geo: str | None = None) -> list[Plan]:
        from sqlalchemy import select

        from ..db.models import BillingPlan as BillingPlanModel

        async with await self._session() as s:
            stmt = select(BillingPlanModel).where(BillingPlanModel.active.is_(True))
            if geo is not None:
                stmt = stmt.where(BillingPlanModel.geo == geo)
            r = await s.execute(stmt)
            return [_row_to_plan(row) for row in r.scalars().all()]

    async def get_subscription(self, *, user_id: str) -> Subscription | None:
        from sqlalchemy import select

        from ..db.models import Subscription as SubscriptionModel

        async with await self._session() as s:
            r = await s.execute(
                select(SubscriptionModel)
                .where(SubscriptionModel.user_id == user_id)
                .order_by(SubscriptionModel.created_at.desc())
                .limit(1)
            )
            row = r.scalar_one_or_none()
            return _row_to_subscription(row) if row is not None else None

    async def get_subscription_by_provider_sub(
        self, *, provider: str, provider_sub_id: str
    ) -> Subscription | None:
        from sqlalchemy import select

        from ..db.models import Subscription as SubscriptionModel

        async with await self._session() as s:
            r = await s.execute(
                select(SubscriptionModel).where(
                    SubscriptionModel.provider == provider,
                    SubscriptionModel.provider_sub_id == provider_sub_id,
                )
            )
            row = r.scalar_one_or_none()
            return _row_to_subscription(row) if row is not None else None

    async def upsert_subscription(self, sub: Subscription) -> Subscription:
        from sqlalchemy import select

        from ..db.models import Subscription as SubscriptionModel

        async with await self._session() as s:
            r = await s.execute(select(SubscriptionModel).where(SubscriptionModel.id == sub.id))
            row = r.scalar_one_or_none()
            if row is None:
                row = SubscriptionModel(id=sub.id)
                s.add(row)
            row.user_id = sub.user_id
            row.plan_slug = sub.plan_slug
            row.provider = (
                sub.provider.value if isinstance(sub.provider, BillingProvider) else sub.provider
            )
            row.provider_sub_id = sub.provider_sub_id
            row.status = (
                sub.status.value if isinstance(sub.status, SubscriptionStatus) else sub.status
            )
            row.current_period_start = sub.current_period_start
            row.current_period_end = sub.current_period_end
            row.cancel_at_period_end = sub.cancel_at_period_end
            row.trial_ends_at = sub.trial_ends_at
            row.billing_country = sub.billing_country
            row.updated_at = _utcnow()
            await s.commit()
            return _row_to_subscription(row)

    async def list_all_subscriptions(self) -> list[Subscription]:
        from sqlalchemy import select

        from ..db.models import Subscription as SubscriptionModel

        async with await self._session() as s:
            r = await s.execute(select(SubscriptionModel))
            return [_row_to_subscription(row) for row in r.scalars().all()]

    async def last_payment_for(self, user_id: str) -> datetime | None:
        from sqlalchemy import select

        from ..db.models import Invoice as InvoiceModel

        async with await self._session() as s:
            r = await s.execute(
                select(InvoiceModel.paid_at)
                .where(InvoiceModel.user_id == user_id, InvoiceModel.paid_at.is_not(None))
                .order_by(InvoiceModel.paid_at.desc())
                .limit(1)
            )
            row = r.scalar_one_or_none()
            return row

    async def list_invoices(self, *, user_id: str) -> list[InvoiceRecord]:
        from sqlalchemy import select

        from ..db.models import Invoice as InvoiceModel

        async with await self._session() as s:
            r = await s.execute(
                select(InvoiceModel)
                .where(InvoiceModel.user_id == user_id)
                .order_by(InvoiceModel.created_at.desc())
            )
            rows = r.scalars().all()
            return [
                InvoiceRecord(
                    id=row.id,
                    user_id=row.user_id,
                    subscription_id=row.subscription_id,
                    provider=row.provider,
                    provider_invoice_id=row.provider_invoice_id,
                    amount_cents=row.amount_cents,
                    currency=row.currency,
                    status=row.status,
                    paid_at=row.paid_at,
                    receipt_url=row.receipt_url,
                    fiscal_receipt_id=row.fiscal_receipt_id,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def insert_invoice(self, inv: InvoiceRecord) -> InvoiceRecord:
        from ..db.models import Invoice as InvoiceModel

        async with await self._session() as s:
            s.add(
                InvoiceModel(
                    id=inv.id,
                    subscription_id=inv.subscription_id,
                    user_id=inv.user_id,
                    provider=inv.provider,
                    provider_invoice_id=inv.provider_invoice_id,
                    amount_cents=inv.amount_cents,
                    currency=inv.currency,
                    status=inv.status,
                    paid_at=inv.paid_at,
                    receipt_url=inv.receipt_url,
                    fiscal_receipt_id=inv.fiscal_receipt_id,
                )
            )
            await s.commit()
            return inv

    async def mark_webhook_processed(self, *, provider: str, event_id: str) -> bool:
        # INSERT ... ON CONFLICT DO NOTHING in one statement — atomic against
        # concurrent redeliveries. RETURNING the row ⇒ newly inserted (True);
        # no row ⇒ already processed (False), handler returns 200 without
        # re-processing.
        from sqlalchemy import text

        async with await self._session() as s:
            r = await s.execute(
                text(
                    "INSERT INTO billing_webhook_events (provider, provider_event_id) "
                    "VALUES (:p, :e) ON CONFLICT DO NOTHING RETURNING provider"
                ),
                {"p": provider, "e": event_id},
            )
            await s.commit()
            return r.first() is not None

    async def get_billing_profile(self, *, user_id: str) -> BillingProfileRecord | None:
        from sqlalchemy import select

        from ..db.models import BillingProfile as BillingProfileModel

        async with await self._session() as s:
            r = await s.execute(
                select(BillingProfileModel).where(BillingProfileModel.user_id == user_id)
            )
            row = r.scalar_one_or_none()
            if row is None:
                return None
            return BillingProfileRecord(
                user_id=row.user_id,
                country=row.country,
                provider=row.provider,
                provider_customer_id=row.provider_customer_id,
                default_payment_method_token=row.default_payment_method_token,
                updated_at=row.updated_at,
            )

    async def upsert_billing_profile(
        self,
        *,
        user_id: str,
        country: str,
        provider: str | None = None,
        provider_customer_id: str | None = None,
    ) -> BillingProfileRecord:
        from sqlalchemy import select

        from ..db.models import BillingProfile as BillingProfileModel

        async with await self._session() as s:
            r = await s.execute(
                select(BillingProfileModel).where(BillingProfileModel.user_id == user_id)
            )
            row = r.scalar_one_or_none()
            if row is None:
                row = BillingProfileModel(user_id=user_id, country=country)
                s.add(row)
            row.country = country
            if provider is not None:
                row.provider = provider
            if provider_customer_id is not None:
                row.provider_customer_id = provider_customer_id
            row.updated_at = _utcnow()
            await s.commit()
            return BillingProfileRecord(
                user_id=row.user_id,
                country=row.country,
                provider=row.provider,
                provider_customer_id=row.provider_customer_id,
                default_payment_method_token=row.default_payment_method_token,
                updated_at=row.updated_at,
            )

    async def set_provider_customer_id(
        self, *, user_id: str, provider: str, provider_customer_id: str
    ) -> None:
        """Attach the provider's customer id to an existing billing profile (see
        the in-memory twin for the rationale). No-op when the profile doesn't
        exist yet — a later webhook that finds it will set it."""
        from sqlalchemy import select

        from ..db.models import BillingProfile as BillingProfileModel

        async with await self._session() as s:
            r = await s.execute(
                select(BillingProfileModel).where(BillingProfileModel.user_id == user_id)
            )
            row = r.scalar_one_or_none()
            if row is None:
                return
            row.provider = provider
            row.provider_customer_id = provider_customer_id
            row.updated_at = _utcnow()
            await s.commit()


def make_billing_store(settings: Settings) -> BillingStore:
    """Pick Postgres when ``COMPANION_USE_DB=1``, else in-memory. Postgres is
    constructed but only connects on first use, so an unreachable DB doesn't
    break startup; per-call failures surface to the caller (router) which
    already redacts."""
    if settings.use_db:
        return PostgresBillingStore(settings)  # type: ignore[return-value]
    return InMemoryBillingStore()  # type: ignore[return-value]


__all__ = [
    "BillingProfileRecord",
    "BillingStore",
    "InMemoryBillingStore",
    "InvoiceRecord",
    "PostgresBillingStore",
    "SEED_PLANS",
    "make_billing_store",
]
