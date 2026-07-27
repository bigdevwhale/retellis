"""``GET /v1/routing`` — the routing + budget dashboard payload.

Returns a ``RoutingState``: the live fallback chain (BYOK omitted; mock last),
the monthly budget rollup (spent / remaining / pct / soft-warn / hard-stop), a
per-provider usage summary (requests / cost / tokens), the last fallback that
occurred this process, and a Langfuse link-out.

Usage reads are best-effort: if the store is unreachable the endpoint still
returns a valid state with zero spend and an empty per-provider table, so the
dashboard always renders.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deps import get_current_principal, get_current_user_id, get_settings, get_store
from ..routing import last_fallback, routing_state

router = APIRouter()


@router.get("/routing")
async def get_routing(
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    settings = get_settings(request)
    store = get_store(request)
    # The dashboard shows the budget for the principal's ACTIVE scope: a family
    # member sees family-wide spend (all members' family turns, summed against
    # the shared family budget — same rollup the stream gate uses), a personal
    # user sees only their ``family_id IS NULL`` rows. Without this, a family
    # member's dashboard undercounted (only their own rows) and a personal
    # user's over-counted (family rows from a prior membership leaked in).
    principal = await get_current_principal(request)
    family_id = getattr(principal, "family_id", None)
    try:
        if family_id:
            records = await store.list_usage_by_family(family_id=family_id)
        else:
            records = [
                r for r in await store.list_usage(user_id=user_id) if r.usage.family_id is None
            ]
    except Exception:
        # Best-effort: dashboard still renders with zero spend.
        records = []
    return routing_state(
        settings=settings,
        records=records,
        fallback_last_turn=last_fallback(user_id),
    ).model_dump()


__all__ = ["router"]
