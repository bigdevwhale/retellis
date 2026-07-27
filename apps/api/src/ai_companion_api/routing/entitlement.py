"""Per-principal entitlement helpers for the budget gate.

The hosted entitlement model has two meters:

- **credits_usd** — the user's prepaid balance, refilled by a billing webhook
  on a successful payment (``AuthStore.set_user_plan``) and decremented per
  turn (``AuthStore.decrement_credits``). The ``out_of_credits`` gate (balance
  <= 0) is the per-user metering.
- **monthly_budget_usd** — the OPERATOR's global monthly spend cap (self-hosted
  default $20), enforced as a hard-stop in ``routers/llm.py``.

For a paying subscriber these two conflict: a Pro subscriber who prepaid $25
would be cut off by the global $20 cap even with credit balance remaining. So
a paid subscriber is metered SOLELY by the credits gate — the budget hard-stop
is skipped for them (their prepaid balance is the entitlement). Free / self-
hosted users keep the budget hard-stop (the operator's generosity ceiling on
free tiers, and the self-hosted cost cap).

``principal.plan`` is the discriminator: billing sets it to the plan slug
(``plus_ww`` / ``pro_ru`` / …) on a successful payment; free tiers carry
``self_hosted_free`` / ``hosted_free``.
"""

from __future__ import annotations

from ai_companion_contracts import Principal

from ..config import Settings

# Plan strings that are NOT paid subscriptions (free tiers). A paid subscriber
# has a billing_plans slug here (``plus_*`` / ``pro_*``).
_FREE_PLANS: frozenset[str] = frozenset({"self_hosted_free", "hosted_free", "", "free"})


def is_paid_subscriber(principal: Principal | None, settings: Settings) -> bool:
    """True iff ``principal`` is a hosted user on a paid billing plan. Only a
    paid subscriber is metered by the credits gate alone (budget hard-stop
    skipped); everyone else keeps the budget hard-stop."""
    if principal is None:
        return False
    if settings.deployment_mode != "hosted":
        return False
    return principal.plan not in _FREE_PLANS


__all__ = ["is_paid_subscriber"]
