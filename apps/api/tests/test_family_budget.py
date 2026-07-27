"""Family budget rollup: the in-memory helper that powers the budget check.

When a turn carries ``family_id``, spend rolls up against the family budget
(per ``family_id``) — not the individual member's personal budget. The two
scopes are disjoint at the row level: a personal turn has ``family_id IS
NULL``; a family turn has ``family_id == F``. This test exercises the helper
directly so the family-rollup is not silently regressed to per-user.
"""

from __future__ import annotations

import pytest
from ai_companion_contracts import Usage

from ai_companion_api.memory import InMemoryStore
from ai_companion_api.routers.llm import _monthly_spend


@pytest.mark.asyncio
async def test_personal_turns_rollup_against_personal_budget() -> None:
    store = InMemoryStore()
    user = "u1"
    for cost in (0.1, 0.2, 0.05):
        await store.add_usage(
            Usage(
                id="u-" + str(cost),
                user_id=user,
                provider_kind="openai",
                model="gpt-4o-mini",
                prompt_tokens=10,
                completion_tokens=10,
                cost_usd=cost,
                family_id=None,
            )
        )
    spend = await _monthly_spend(store, user_id=user, family_id=None)
    assert abs(spend - 0.35) < 1e-9


@pytest.mark.asyncio
async def test_family_turns_rollup_against_family_budget() -> None:
    store = InMemoryStore()
    user = "u1"
    fam = "fam-1"
    for cost in (0.5, 0.5):
        await store.add_usage(
            Usage(
                id="f-" + str(cost),
                user_id=user,
                provider_kind="openai",
                model="gpt-4o-mini",
                prompt_tokens=10,
                completion_tokens=10,
                cost_usd=cost,
                family_id=fam,
            )
        )
    # Personal turn — must NOT inflate the family rollup.
    await store.add_usage(
        Usage(
            id="p-1",
            user_id=user,
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=99.0,
            family_id=None,
        )
    )
    spend = await _monthly_spend(store, user_id=user, family_id=fam)
    assert abs(spend - 1.0) < 1e-9, (
        f"family rollup leaked personal spend: got {spend}, expected 1.0"
    )


@pytest.mark.asyncio
async def test_personal_rollup_excludes_family_turns() -> None:
    store = InMemoryStore()
    user = "u1"
    fam = "fam-1"
    await store.add_usage(
        Usage(
            id="f-99",
            user_id=user,
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=99.0,
            family_id=fam,
        )
    )
    spend = await _monthly_spend(store, user_id=user, family_id=None)
    assert spend == 0.0


@pytest.mark.asyncio
async def test_other_members_family_turns_count_in_family_rollup() -> None:
    """The family budget gate is FAMILY-WIDE: a turn by any member rolls up
    against the shared family budget, so the hard-stop fires when the FAMILY
    hits the cap — not when each individual member does (which would let a
    family of N spend N× the cap). ``_monthly_spend`` for a family turn must
    therefore aggregate usage across ALL members, not just the requesting
    member. Here ``other`` spent 5.0 on a family turn; ``me``'s family-scoped
    rollup must include it (the gate runs on this number when ``me`` takes the
    next family turn)."""
    store = InMemoryStore()
    me = "me"
    other = "other"
    fam = "fam-1"
    await store.add_usage(
        Usage(
            id="o-1",
            user_id=other,
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=5.0,
            family_id=fam,
        )
    )
    spend = await _monthly_spend(store, user_id=me, family_id=fam)
    assert abs(spend - 5.0) < 1e-9, (
        f"family rollup missed another member's family spend: got {spend}, expected 5.0"
    )


@pytest.mark.asyncio
async def test_list_usage_by_family_aggregates_across_members() -> None:
    """The store-level family-wide rollup used by /v1/routing and the budget
    gate. Returns every row tagged ``family_id == F`` regardless of which
    member incurred it, and excludes personal (family_id IS NULL) rows."""
    store = InMemoryStore()
    fam = "fam-1"
    # Two members, three family turns + one personal turn + one other-family turn.
    for uid, uid_cost in (("me", 1.0), ("other", 2.0)):
        await store.add_usage(
            Usage(
                id=f"{uid}-fam",
                user_id=uid,
                provider_kind="openai",
                model="gpt-4o-mini",
                prompt_tokens=10,
                completion_tokens=10,
                cost_usd=uid_cost,
                family_id=fam,
            )
        )
    await store.add_usage(
        Usage(
            id="me-pers",
            user_id="me",
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=99.0,
            family_id=None,
        )
    )
    await store.add_usage(
        Usage(
            id="me-other-fam",
            user_id="me",
            provider_kind="openai",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=99.0,
            family_id="fam-other",
        )
    )
    rows = await store.list_usage_by_family(family_id=fam)
    assert {r.usage.user_id for r in rows} == {"me", "other"}
    assert all(r.usage.family_id == fam for r in rows)
    assert abs(sum(r.usage.cost_usd for r in rows) - 3.0) < 1e-9
