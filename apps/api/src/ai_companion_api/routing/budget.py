"""Monthly spend budget — soft-warn at 80%, hard-stop at 100%.

The hard-stop is enforced in the stream (``routers/llm.py``): when reached, the
real provider is skipped and the turn falls through to the mock stand-in so the
user still gets a reply. The soft-warn is informational — surfaced on the
routing dashboard and via the ``warn`` flag on ``RoutingState`` — and does not
interrupt the turn.

``compute_budget`` is pure: it takes the already-aggregated monthly spend and
the configured cap, returns a ``BudgetState``. Month aggregation lives in the
store (``MemoryStore.list_usage``) and is rolled up in ``routing/router.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Soft-warn / hard-stop thresholds (fraction of the monthly cap).
SOFT_WARN_FRAC = 0.80
HARD_STOP_FRAC = 1.0


@dataclass
class BudgetState:
    spent_usd: float
    monthly_budget_usd: float
    remaining_usd: float
    pct: float  # spent / cap, 0..1 (clamped at 1.0 for display only here)
    warn: bool  # pct >= 0.80
    hard_stop: bool  # pct >= 1.0


def compute_budget(*, spent_usd: float, monthly_budget_usd: float) -> BudgetState:
    """Pure budget calculation from the month's spend and the configured cap."""
    cap = monthly_budget_usd
    if cap <= 0:
        # No cap configured → never warn, never hard-stop.
        return BudgetState(
            spent_usd=spent_usd,
            monthly_budget_usd=cap,
            remaining_usd=0.0,
            pct=0.0,
            warn=False,
            hard_stop=False,
        )
    pct = spent_usd / cap
    return BudgetState(
        spent_usd=spent_usd,
        monthly_budget_usd=cap,
        remaining_usd=max(0.0, cap - spent_usd),
        pct=pct,
        warn=pct >= SOFT_WARN_FRAC,
        hard_stop=pct >= HARD_STOP_FRAC,
    )


__all__ = ["BudgetState", "compute_budget", "SOFT_WARN_FRAC", "HARD_STOP_FRAC"]
