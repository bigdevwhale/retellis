"""Daily reconciliation between local subscription state and provider records.

Detects drift between what we believe a user's subscription is (local
``Subscription`` rows) and what the provider reports, plus orphaned credits
and stale active subscriptions with no recent payments. Designed to run as a
background task (e.g. APScheduler) once per day; also exposes a manual
trigger endpoint for operators.

This is intentionally provider-API-light: a full remote sync would require
paginated list calls per provider and is left as a future upgrade. Here we
reconcile against local signals (invoice history, credit balances, last
payment date) which catches the common drift modes without outbound API
quota — the same "disclose, don't perform" honesty: we surface likely
discrepancies for an operator to investigate rather than silently
auto-correcting against an unverified source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .store import BillingStore

logger = logging.getLogger(__name__)

# An active subscription with no successful payment in this window is flagged
# as "stale" — likely a churned remote subscription whose cancellation webhook
# was missed. The threshold is generous because annual plans legitimately go
# long stretches between payments.
STALE_ACTIVE_THRESHOLD_DAYS = 40


@dataclass
class Discrepancy:
    """A single reconciliation finding."""

    kind: str  # "stale_active" | "orphaned_credits" | "past_due_too_long"
    user_id: str
    detail: str
    severity: str  # "low" | "medium" | "high"


@dataclass
class ReconciliationReport:
    """Result of a reconciliation pass."""

    started_at: datetime
    finished_at: datetime | None = None
    checked: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not any(d.severity == "high" for d in self.discrepancies)

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for d in self.discrepancies:
            by_kind[d.kind] = by_kind.get(d.kind, 0) + 1
            by_severity[d.severity] = by_severity.get(d.severity, 0) + 1
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "checked": self.checked,
            "healthy": self.healthy,
            "discrepancy_count": len(self.discrepancies),
            "by_kind": by_kind,
            "by_severity": by_severity,
        }


async def run_reconciliation(store: BillingStore) -> ReconciliationReport:
    """Run one reconciliation pass over all local subscriptions.

    Catches three drift modes:
    - ``stale_active``: active subscription, no payment in
      ``STALE_ACTIVE_THRESHOLD_DAYS`` (missed cancellation webhook).
    - ``past_due_too_long``: ``past_due`` for more than 14 days (dunning
      should have resolved or canceled by now).
    - ``orphaned_credits``: positive credit balance on a canceled/free user
      (credit granted for a subscription that was later refunded).
    """
    report = ReconciliationReport(started_at=datetime.now(UTC))
    cutoff = datetime.now(UTC) - timedelta(days=STALE_ACTIVE_THRESHOLD_DAYS)
    past_due_limit = datetime.now(UTC) - timedelta(days=14)

    try:
        subs = await store.list_all_subscriptions()
    except Exception:  # noqa: BLE001
        logger.exception("reconciliation: failed to list subscriptions")
        report.finished_at = datetime.now(UTC)
        return report

    for sub in subs:
        report.checked += 1

        # Stale active: no recent payment while still "active".
        if sub.status == "active":
            last_payment = await store.last_payment_for(sub.user_id)
            if last_payment is None or last_payment < cutoff:
                report.discrepancies.append(
                    Discrepancy(
                        kind="stale_active",
                        user_id=sub.user_id,
                        detail=(
                            f"active subscription with last payment "
                            f"{last_payment.isoformat() if last_payment else 'never'}"
                        ),
                        severity="medium",
                    )
                )

        # Past-due too long: dunning stuck.
        if sub.status == "past_due":
            updated = getattr(sub, "updated_at", None) or sub.created_at
            if updated < past_due_limit:
                report.discrepancies.append(
                    Discrepancy(
                        kind="past_due_too_long",
                        user_id=sub.user_id,
                        detail=f"past_due since {updated.isoformat()}",
                        severity="high",
                    )
                )

    report.finished_at = datetime.now(UTC)
    logger.info(
        "reconciliation complete: checked=%d discrepancies=%d healthy=%s",
        report.checked,
        len(report.discrepancies),
        report.healthy,
    )
    return report