"""Routing state for the dashboard — chain viz, budget, per-provider summary.

``GET /v1/routing`` returns a ``RoutingState`` assembled here from:

- the **chain** — the live fallback ladder the stream walks (BYOK → env → Ollama),
  minus the per-turn BYOK node (the dashboard is not tied to a turn);
- the **budget** — current-month spend vs ``monthly_budget_usd`` with soft-warn
  (≥80%) and hard-stop (≥100%) flags;
- the **per-provider summary** — requests / cost / tokens rolled up from the
  ``usage`` rows;
- a **Langfuse link-out** to the browser-facing trace UI.

The chain shows configured providers only. Unconfigured env providers are
absent from the chain — they are not tried. Ollama is always shown: ``standby``
when no ``ollama_base_url`` is set (the documented last-resort, not live),
``healthy`` when configured.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_companion_contracts import ProviderSummary, RoutingNode, RoutingState

from ..config import Settings
from ..llm.provider import DEFAULT_MODELS, configured_env_kinds
from ..memory.store import UsageRecord
from .budget import BudgetState, compute_budget


def _kind_status(settings: Settings, kind: str) -> str:
    """Dashboard status for a provider kind given current settings."""
    if kind == "ollama":
        return "healthy" if settings.ollama_base_url else "standby"
    # env kind — healthy iff still configured, else unavailable (it has usage
    # from when it was configured, but is no longer in the live chain).
    return "healthy" if kind in configured_env_kinds(settings) else "unavailable"


def display_chain(settings: Settings) -> list[RoutingNode]:
    """The live fallback ladder for the dashboard (BYOK omitted)."""
    nodes: list[RoutingNode] = []
    for kind in configured_env_kinds(settings):
        nodes.append(
            RoutingNode(kind=kind, model=DEFAULT_MODELS[kind], base_url=None, status="healthy")
        )
    # Only show Ollama if it's configured
    if settings.ollama_base_url:
        nodes.append(
            RoutingNode(
                kind="ollama",
                model=DEFAULT_MODELS["ollama"],
                base_url=settings.ollama_base_url,
                status="healthy",
            )
        )
    return nodes


def _monthly_spend(records: list[UsageRecord], *, now: datetime) -> float:
    """Sum cost_usd over records in the same calendar month as ``now``."""
    return sum(
        r.usage.cost_usd
        for r in records
        if r.created_at.year == now.year and r.created_at.month == now.month
    )


def summarize_usage(*, settings: Settings, records: list[UsageRecord]) -> list[ProviderSummary]:
    """Roll up usage rows into a per-provider summary, in chain order then extras."""
    # Bucket by kind, preserving the most recent model per kind.
    buckets: dict[str, dict[str, object]] = {}
    for r in records:
        k = r.usage.provider_kind
        b = buckets.setdefault(
            k,
            {
                "requests": 0,
                "cost_usd": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "model": r.usage.model,
            },
        )
        b["requests"] = int(b["requests"]) + 1  # type: ignore[operator]
        b["cost_usd"] = float(b["cost_usd"]) + float(r.usage.cost_usd)  # type: ignore[operator]
        b["tokens_in"] = int(b["tokens_in"]) + int(r.usage.prompt_tokens)  # type: ignore[operator]
        b["tokens_out"] = int(b["tokens_out"]) + int(r.usage.completion_tokens)  # type: ignore[operator]
        b["model"] = r.usage.model  # most recent model name wins

    # Order: chain order (env kinds + ollama + mock), then any leftover kinds.
    order = [k for k in configured_env_kinds(settings)] + ["ollama", "mock"]
    seen: set[str] = set()
    rows: list[ProviderSummary] = []
    for kind in order:
        if kind in buckets and kind not in seen:
            rows.append(_summary_row(settings, kind, buckets[kind]))
            seen.add(kind)
    for kind, b in buckets.items():
        if kind not in seen:
            rows.append(_summary_row(settings, kind, b))
            seen.add(kind)
    return rows


def _summary_row(settings: Settings, kind: str, b: dict[str, object]) -> ProviderSummary:
    return ProviderSummary(
        kind=kind,
        model=str(b["model"]),
        requests=int(b["requests"]),  # type: ignore[arg-type]
        cost_usd=float(b["cost_usd"]),  # type: ignore[arg-type]
        tokens_in=int(b["tokens_in"]),  # type: ignore[arg-type]
        tokens_out=int(b["tokens_out"]),  # type: ignore[arg-type]
        status=_kind_status(settings, kind),  # type: ignore[arg-type]
    )


def routing_state(
    *,
    settings: Settings,
    records: list[UsageRecord],
    fallback_last_turn: str | None,
    now: datetime | None = None,
) -> RoutingState:
    """Assemble the full ``RoutingState`` for ``GET /v1/routing``."""
    now = now or datetime.now(UTC)
    spent = _monthly_spend(records, now=now)
    budget: BudgetState = compute_budget(
        spent_usd=spent, monthly_budget_usd=settings.monthly_budget_usd
    )
    return RoutingState(
        chain=display_chain(settings),
        monthly_budget_usd=settings.monthly_budget_usd,
        spent_usd=spent,
        remaining_usd=budget.remaining_usd,
        fallback_last_turn=fallback_last_turn,
        pct=budget.pct,
        warn=budget.warn,
        hard_stop=budget.hard_stop,
        per_provider=summarize_usage(settings=settings, records=records),
        langfuse_url=settings.langfuse_public_url or None,
    )


__all__ = ["display_chain", "routing_state", "summarize_usage"]
