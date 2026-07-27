"""Sprint 6 M2 — atomic hosted-credits debit.

``AuthStore.decrement_credits`` is the atomic conditional debit: a single
``UPDATE users SET credits_usd = credits_usd - :amt WHERE id = :u AND
credits_usd >= :amt RETURNING credits_usd`` (Postgres) / an equivalent
check-then-subtract (in-memory). It returns ``True`` when the debit happened
and ``False`` when the balance didn't cover it — so concurrent turns can't
double-debit the balance below zero.

The in-memory test (always runs) fires N concurrent debits against a balance
that covers fewer than N and asserts exactly the covered number succeed and the
balance never goes negative. The Postgres test is gated on
``COMPANION_USE_DB=1`` (the eval/CI default is in-memory) and asserts the same
under real row-level locking.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from ai_companion_api.auth.store import InMemoryAuthStore


async def _seed(store, *, user_id: str, credits: float) -> None:
    """Create a user with a forced id + balance via the public store API.

    ``create_user`` generates a random id, so for the in-memory store we poke
    the balance on the returned record directly (the test owns the in-memory
    store). For Postgres the helper below uses a real row."""
    user = await store.create_user(
        issuer="local",
        subject=f"{user_id}@x.com",
        email=f"{user_id}@x.com",
        display_name=user_id,
        password_hash=None,
        plan="hosted_free",
        credits_usd=credits,
    )
    return user


async def test_inmemory_atomic_debit_no_double_spend() -> None:
    store = InMemoryAuthStore()
    user = await _seed(store, user_id="u", credits=1.0)
    # 10 concurrent debits of 0.3 against a 1.0 balance → at most 3 succeed
    # (1.0 / 0.3 = 3 with remainder 0.1). The balance must never go negative
    # and exactly 3 debits must commit.
    results = await asyncio.gather(
        *[store.decrement_credits(user_id=user.id, amount=0.3) for _ in range(10)]
    )
    successes = sum(1 for r in results if r)
    assert successes == 3
    # Refresh from the store's authoritative record.
    fresh = await store.get_user(user.id)
    assert fresh is not None
    assert fresh.credits_usd == pytest.approx(0.1, abs=1e-9)


async def test_inmemory_atomic_debit_insufficient_returns_false() -> None:
    store = InMemoryAuthStore()
    user = await _seed(store, user_id="u", credits=0.2)
    # 0.2 balance, debit 0.5 → False, balance unchanged.
    ok = await store.decrement_credits(user_id=user.id, amount=0.5)
    assert ok is False
    fresh = await store.get_user(user.id)
    assert fresh is not None
    assert fresh.credits_usd == pytest.approx(0.2, abs=1e-9)


async def test_inmemory_debit_zero_or_negative_is_noop() -> None:
    store = InMemoryAuthStore()
    user = await _seed(store, user_id="u", credits=5.0)
    assert (await store.decrement_credits(user_id=user.id, amount=0.0)) is False
    assert (await store.decrement_credits(user_id=user.id, amount=-1.0)) is False
    fresh = await store.get_user(user.id)
    assert fresh is not None
    assert fresh.credits_usd == pytest.approx(5.0, abs=1e-9)


@pytest.mark.skipif(
    os.environ.get("COMPANION_USE_DB") != "1",
    reason="Postgres credits test needs COMPANION_USE_DB=1 + a live DB",
)
async def test_postgres_atomic_debit_no_double_spend(make_app, app_client) -> None:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as c:
            yield c, app

    async with _ctx() as (_ac, app):
        store = app.state.auth_store
        # Create a hosted user with credits directly via the store (the local
        # backend grants 0 credits; we need a positive balance to debit).
        user = await store.create_user(
            issuer="local",
            subject="credits@x.com",
            email="credits@x.com",
            display_name="Credits",
            password_hash=None,
            plan="hosted_free",
            credits_usd=1.0,
        )
        results = await asyncio.gather(
            *[store.decrement_credits(user_id=user.id, amount=0.3) for _ in range(10)]
        )
        successes = sum(1 for r in results if r)
        assert successes == 3
        fresh = await store.get_user(user.id)
        assert fresh is not None
        assert fresh.credits_usd == pytest.approx(0.1, abs=1e-6)
