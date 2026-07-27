"""Phase 2a: time-based salience decay + reinforcement on recall.

Decay is applied at read time (no cron, no row mutation): effective salience
halves every ~30 days, emotionally intense events fade slower, and nothing
decays below the floor. Reinforcement is the write-side counterpart: events
that actually surfaced into a turn's context get a small capped bump.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ai_companion_contracts import Event, EventRole

from ai_companion_api.memory import InMemoryStore, append_event
from ai_companion_api.memory.recall import effective_salience, rank_and_chain

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _evt(i: int, *, salience: float, age_days: float, intensity: float = 0.0) -> Event:
    return Event(
        id=f"e{i}",
        user_id="u",
        persona_id="p",
        role=EventRole.user,
        content=f"msg {i}",
        salience=salience,
        emotional_intensity=intensity,
        created_at=NOW - timedelta(days=age_days),
    )


def test_fresh_event_does_not_decay() -> None:
    assert effective_salience(_evt(1, salience=0.8, age_days=0), NOW) == pytest.approx(0.8)


def test_no_created_at_means_no_decay() -> None:
    e = Event(id="x", user_id="u", persona_id="p", role=EventRole.user, content="m", salience=0.7)
    assert effective_salience(e, NOW) == pytest.approx(0.7)


def test_old_neutral_event_decays_to_floor() -> None:
    # 300 days at a 30-day half-life → raw factor ~0.001, clamped to the 0.2 floor.
    out = effective_salience(_evt(1, salience=1.0, age_days=300), NOW)
    assert out == pytest.approx(0.2)


def test_intense_event_decays_slower_than_neutral() -> None:
    neutral = effective_salience(_evt(1, salience=0.8, age_days=60, intensity=0.0), NOW)
    intense = effective_salience(_evt(2, salience=0.8, age_days=60, intensity=1.0), NOW)
    assert intense > neutral
    # 60 days: neutral half-life 30d → 0.25 factor (above floor); intense
    # half-life 120d → ~0.71 factor.
    assert neutral == pytest.approx(0.8 * 0.25)
    assert intense == pytest.approx(0.8 * 0.5 ** (60 / 120))


def test_rank_prefers_fresh_over_stale_at_equal_salience() -> None:
    # Same content (identical cosine), same stored salience, same list position
    # apart from order — the stale event's decayed salience should lose.
    stale = _evt(1, salience=0.9, age_days=200)
    fresh = _evt(2, salience=0.9, age_days=0)
    chains = rank_and_chain([stale, fresh], "msg", k=1, now=NOW)
    assert chains
    assert chains[0].events[-1].id == "e2"


@pytest.mark.asyncio
async def test_reinforce_bumps_and_caps() -> None:
    store = InMemoryStore()
    ev = await append_event(
        store,
        user_id="u",
        persona_id="p",
        convo_id="c",
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    base = ev.salience
    await store.reinforce_events(user_id="u", event_ids=[ev.id], boost=0.05)
    rows = await store.list_events(user_id="u", persona_id="p")
    assert rows[0].salience == pytest.approx(min(1.0, base + 0.05))
    # Repeated reinforcement never exceeds the cap.
    for _ in range(40):
        await store.reinforce_events(user_id="u", event_ids=[ev.id], boost=0.05)
    rows = await store.list_events(user_id="u", persona_id="p")
    assert rows[0].salience == 1.0


@pytest.mark.asyncio
async def test_reinforce_is_scoped_by_user() -> None:
    store = InMemoryStore()
    ev = await append_event(
        store,
        user_id="u1",
        persona_id="p",
        convo_id="c",
        role=EventRole.user,
        content="private moment",
    )
    base = ev.salience
    # Another user holding this event id cannot bump it.
    await store.reinforce_events(user_id="u2", event_ids=[ev.id], boost=0.5)
    rows = await store.list_events(user_id="u1", persona_id="p")
    assert rows[0].salience == pytest.approx(base)
