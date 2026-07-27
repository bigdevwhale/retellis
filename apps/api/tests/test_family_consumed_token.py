"""``consume_invite_token`` — the single-use replay defense (PLAN §16 #2).

The family-invite token is sealed before being emailed; only the hash lands
in the store. The accept endpoint calls ``consume_invite_token`` BEFORE
looking up the invite, so a replayed token 410s immediately regardless of
the invite row's state (active, accepted, deleted, expired). The
``consumed_tokens`` table is the authoritative replay defense; the
in-memory store uses a set with the same semantics.

These tests exercise the in-memory implementation directly so a regression
in the protocol contract (e.g. accidentally making it idempotent on the
"first" side) is caught without spinning up the HTTP layer.
"""

from __future__ import annotations

import hashlib

import pytest

from ai_companion_api.family.store import InMemoryFamilyStore


def _hash(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_consume_invite_token_first_call_wins() -> None:
    store = InMemoryFamilyStore()
    h = _hash("token-A")
    assert await store.consume_invite_token(token_hash=h) is True


@pytest.mark.asyncio
async def test_consume_invite_token_replay_returns_false() -> None:
    store = InMemoryFamilyStore()
    h = _hash("token-B")
    assert await store.consume_invite_token(token_hash=h) is True
    # Replay — the same hash MUST NOT be re-recorded. The router uses this
    # false to raise 410.
    assert await store.consume_invite_token(token_hash=h) is False
    # And every subsequent replay still returns False.
    assert await store.consume_invite_token(token_hash=h) is False


@pytest.mark.asyncio
async def test_consume_invite_token_distinct_hashes_independent() -> None:
    store = InMemoryFamilyStore()
    h1 = _hash("token-C-1")
    h2 = _hash("token-C-2")
    assert await store.consume_invite_token(token_hash=h1) is True
    assert await store.consume_invite_token(token_hash=h2) is True
    # Replays of each are still blocked.
    assert await store.consume_invite_token(token_hash=h1) is False
    assert await store.consume_invite_token(token_hash=h2) is False


@pytest.mark.asyncio
async def test_consume_invite_token_does_not_block_lookups() -> None:
    """The consume call is the replay defense — it does NOT remove the
    invite row. The router calls it BEFORE ``get_invite_by_hash`` so the
    lookup still finds the row on the first accept."""
    from datetime import UTC, datetime, timedelta

    from ai_companion_contracts import FamilyRole

    store = InMemoryFamilyStore()
    fam = await store.create_family(name="F", owner_user_id="owner")
    h = _hash("token-D")
    invite = await store.create_invite(
        family_id=fam.id,
        email="invitee@x.com",
        role=FamilyRole.member,
        token_hash=h,
        invited_by="owner",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    # The invite row is present.
    found = await store.get_invite_by_hash(token_hash=h)
    assert found is not None
    assert found.id == invite.id
    # Consume — the invite row is still there; only the consumed_tokens set
    # has been updated.
    assert await store.consume_invite_token(token_hash=h) is True
    still_there = await store.get_invite_by_hash(token_hash=h)
    assert still_there is not None
    assert still_there.id == invite.id
    # And the replay is blocked.
    assert await store.consume_invite_token(token_hash=h) is False
