"""Routing, fallback, and budget — Phase 4.

- ``budget`` — monthly spend vs cap; soft-warn at 80%, hard-stop at 100%.
- ``router`` — assembles ``RoutingState`` for the dashboard from the chain + usage.
- ``fallback`` — the chain runner that walks candidates and falls over on failure.

The live chain is built in ``llm.provider.build_chain`` (BYOK → env → Ollama →
mock); this package owns the budget math, the dashboard rollup, and the runner.
"""

from .budget import BudgetState, compute_budget
from .fallback import (
    clear_fallback,
    last_fallback,
    record_fallback,
    run_with_fallback,
)
from .router import display_chain, routing_state, summarize_usage

__all__ = [
    "BudgetState",
    "clear_fallback",
    "compute_budget",
    "display_chain",
    "last_fallback",
    "record_fallback",
    "routing_state",
    "run_with_fallback",
    "summarize_usage",
]
