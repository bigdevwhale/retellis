"""Fallback chain runner — walk the candidates, fall over on failure.

``run_with_fallback`` tries each ``RoutingCandidate`` in order. On a
``LlmCallError`` (provider 429 / 5xx / timeout / connection refused) from a
real provider it yields a ``("fallback", (from_kind, to_kind, reason))`` tag and
continues to the next candidate. The chain always ends in the mock stand-in, so
a turn always completes; if the mock itself raises (it never should) the error
propagates honestly rather than being swallowed.

The runner also records the last fallback per user (``fallback_last_turn``) so
the routing dashboard can show "openai → mock (rate-limited)" without keeping
per-request logs.

Budget hard-stop is *not* handled here — the caller (``routers/llm.py``)
truncates the chain to ``[mock]`` and emits a single ``fallback`` event with
reason ``"budget hard-stop"`` before invoking the runner. This keeps the runner
pure: it walks whatever chain it is given.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..llm import LlmCallError
from ..llm.provider import RoutingCandidate
from ..observability import redact

# Process-local last-fallback tracker, keyed by user_id. MVP single-user; a
# Postgres-backed rollup is post-MVP. Cleared by tests via ``clear_fallback``.
_FALLBACK_LAST: dict[str, str] = {}


def record_fallback(user_id: str, desc: str) -> None:
    _FALLBACK_LAST[user_id] = desc


def last_fallback(user_id: str) -> str | None:
    return _FALLBACK_LAST.get(user_id)


def clear_fallback(user_id: str) -> None:
    _FALLBACK_LAST.pop(user_id, None)


async def run_with_fallback(
    candidates: list[RoutingCandidate],
    messages: list[dict[str, str]],
    *,
    user_id: str,
) -> AsyncIterator[tuple[str, object]]:
    """Walk the chain. Yields:

    - ``("token", str)`` for each streamed token,
    - ``("fallback", (from_kind, to_kind, reason))`` when a real provider fails
      and the chain advances,
    - ``("served", RoutingCandidate)`` for the candidate that completed the turn
      (so the caller can read its ``last_usage()`` for the usage event).

    The final candidate must be the mock stand-in. A successful candidate ends
    the turn; a failed real candidate yields a fallback tag and continues.
    """
    for i, cand in enumerate(candidates):
        try:
            async for tok in cand.adapter.stream(messages, cand.model):
                yield ("token", tok)
            yield ("served", cand)
            return  # success — stop the chain
        except LlmCallError as exc:
            if cand.is_mock:
                # The mock stand-in never calls a network — if it raised, the
                # process is in a state we won't paper over. Propagate honestly.
                raise
            nxt = candidates[i + 1] if i + 1 < len(candidates) else None
            if nxt is None:
                raise  # nothing left to fall back to (shouldn't happen; mock is last)
            reason = redact(str(exc))
            yield ("fallback", (cand.kind, nxt.kind, reason))
            record_fallback(user_id, f"{cand.kind} → {nxt.kind} ({reason})")
            continue


__all__ = [
    "clear_fallback",
    "last_fallback",
    "record_fallback",
    "run_with_fallback",
]
