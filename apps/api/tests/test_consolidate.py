"""Phase 2c: episodic consolidation — old event stretches → episode memories.

The LLM call is stubbed via a fake adapter. Invariants under test: the
threshold gates the pass (no-op on short convos), the oldest uncovered batch is
summarized with full provenance, coverage makes re-runs idempotent, raw events
survive, and mock/unparseable paths are silent no-ops.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ai_companion_contracts import EventRole, Memory, MemoryStatus

from ai_companion_api.memory import InMemoryStore, append_event
from ai_companion_api.memory.consolidate import (
    CONSOLIDATE_BATCH,
    CONSOLIDATE_MIN_UNCOVERED,
    ERA_BATCH,
    ERA_MIN_EPISODES,
    RECENT_KEEP,
    maybe_consolidate,
    maybe_consolidate_eras,
)

USER = "u-cons"
PERSONA = "companion"
CONVO = "c-cons"

_REPLY = '{"summary": "You went through a hard job change and were anxious for weeks.", "tags": ["Work", "anxiety"], "salience": 0.7}'


class _FakeAdapter:
    provider_kind = "openai"

    def __init__(self, reply: str = _REPLY, raises: bool = False) -> None:
        self._reply = reply
        self._raises = raises
        self.calls = 0

    async def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return self._reply


class _MockAdapter:
    provider_kind = "mock"


async def _seed(store: InMemoryStore, n: int) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        ev = await append_event(
            store,
            user_id=USER,
            persona_id=PERSONA,
            convo_id=CONVO,
            role=EventRole.user if i % 2 == 0 else EventRole.assistant,
            content=f"turn {i}: work was stressful",
        )
        ids.append(ev.id)
    return ids


@pytest.mark.asyncio
async def test_below_threshold_is_noop() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED - 1)
    a = _FakeAdapter()
    out = await maybe_consolidate(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA, convo_id=CONVO
    )
    assert out is None
    assert a.calls == 0


@pytest.mark.asyncio
async def test_consolidates_oldest_batch_with_provenance() -> None:
    store = InMemoryStore()
    total = RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED + 5
    ids = await _seed(store, total)
    a = _FakeAdapter()
    mem = await maybe_consolidate(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA, convo_id=CONVO
    )
    assert mem is not None
    assert a.calls == 1
    assert "episode" in mem.tags
    assert "work" in mem.tags  # lowercased by the parser
    assert mem.salience == pytest.approx(0.7)
    # Oldest uncovered events, bounded by the batch size, never the fresh tail.
    expect_n = min(total - RECENT_KEEP, CONSOLIDATE_BATCH)
    assert mem.source_event_ids == ids[:expect_n]
    # Raw events survive — the chain stays the recall substrate.
    events = await store.list_events(user_id=USER, persona_id=PERSONA, limit=1000)
    assert len(events) == total
    # The episode memory is persisted and visible.
    mems = await store.list_memories(user_id=USER, persona_id=PERSONA)
    assert any(m.id == mem.id for m in mems)


@pytest.mark.asyncio
async def test_covered_batch_makes_second_run_noop() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED)
    a = _FakeAdapter()
    first = await maybe_consolidate(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA, convo_id=CONVO
    )
    assert first is not None
    second = await maybe_consolidate(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA, convo_id=CONVO
    )
    assert second is None  # everything old is covered now
    assert a.calls == 1


@pytest.mark.asyncio
async def test_mock_adapter_is_noop() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED + 5)
    out = await maybe_consolidate(
        _MockAdapter(), "mock", store, user_id=USER, persona_id=PERSONA, convo_id=CONVO
    )
    assert out is None


@pytest.mark.asyncio
async def test_unparseable_reply_is_noop() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED + 5)
    out = await maybe_consolidate(
        _FakeAdapter(reply="sorry, no json"),
        "gpt-4o-mini",
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
    )
    assert out is None
    assert (await store.list_memories(user_id=USER, persona_id=PERSONA)) == []


@pytest.mark.asyncio
async def test_llm_error_is_noop() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED + 5)
    out = await maybe_consolidate(
        _FakeAdapter(raises=True),
        "gpt-4o-mini",
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
    )
    assert out is None


# --- Phase 3b: era consolidation (episodes → eras) ----------------------------

_ERA_REPLY = (
    '{"summary": "That spring you changed jobs and slowly found your footing.", '
    '"tags": ["Work", "change"], "salience": 0.8}'
)


async def _seed_episodes(store: InMemoryStore, n: int, *, days_apart: int = 1) -> list[Memory]:
    out: list[Memory] = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        ts = base + timedelta(days=i * days_apart)
        m = Memory(
            id=uuid.uuid4().hex,
            user_id=USER,
            persona_id=PERSONA,
            content=f"episode {i}: you were stressed about work",
            tags=["episode", "work"],
            salience=0.5,
            source_event_ids=[f"ev-{i}-a", f"ev-{i}-b"],
            status=MemoryStatus.active,
            created_at=ts,
            updated_at=ts,
        )
        await store.add_memory(m)
        out.append(m)
    return out


@pytest.mark.asyncio
async def test_era_below_threshold_is_noop() -> None:
    store = InMemoryStore()
    await _seed_episodes(store, ERA_MIN_EPISODES - 1)
    a = _FakeAdapter(reply=_ERA_REPLY)
    out = await maybe_consolidate_eras(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA
    )
    assert out is None
    assert a.calls == 0


@pytest.mark.asyncio
async def test_era_compresses_oldest_episodes_and_supersedes_them() -> None:
    store = InMemoryStore()
    total = ERA_MIN_EPISODES + 2
    episodes = await _seed_episodes(store, total)
    a = _FakeAdapter(reply=_ERA_REPLY)
    era = await maybe_consolidate_eras(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA
    )
    assert era is not None
    assert a.calls == 1
    assert "era" in era.tags and "work" in era.tags
    assert era.salience == pytest.approx(0.8)
    # Provenance: union of the constituent episodes' source events (oldest batch).
    batch = episodes[: min(total, ERA_BATCH)]
    expect_ids = [i for m in batch for i in m.source_event_ids]
    assert era.source_event_ids == expect_ids
    # Constituents left the active set; the era replaced them.
    active = await store.list_memories(user_id=USER, persona_id=PERSONA)
    active_ids = {m.id for m in active}
    assert era.id in active_ids
    for m in batch:
        assert m.id not in active_ids
    # Anything beyond the batch stays active.
    for m in episodes[len(batch) :]:
        assert m.id in active_ids


@pytest.mark.asyncio
async def test_era_second_run_is_noop_until_new_episodes_accumulate() -> None:
    store = InMemoryStore()
    await _seed_episodes(store, ERA_MIN_EPISODES)
    a = _FakeAdapter(reply=_ERA_REPLY)
    first = await maybe_consolidate_eras(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA
    )
    assert first is not None
    second = await maybe_consolidate_eras(
        a, "gpt-4o-mini", store, user_id=USER, persona_id=PERSONA
    )
    assert second is None  # constituents superseded — below threshold again
    assert a.calls == 1


@pytest.mark.asyncio
async def test_era_mock_adapter_is_noop() -> None:
    store = InMemoryStore()
    await _seed_episodes(store, ERA_MIN_EPISODES + 1)
    out = await maybe_consolidate_eras(
        _MockAdapter(), "mock", store, user_id=USER, persona_id=PERSONA
    )
    assert out is None


@pytest.mark.asyncio
async def test_zero_salience_falls_back_to_peak_event() -> None:
    store = InMemoryStore()
    await _seed(store, RECENT_KEEP + CONSOLIDATE_MIN_UNCOVERED)
    reply = '{"summary": "You had a quiet stretch.", "tags": [], "salience": 0}'
    mem = await maybe_consolidate(
        _FakeAdapter(reply=reply),
        "gpt-4o-mini",
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
    )
    assert mem is not None
    # Discounted peak of the constituent events, never zero for a real batch.
    assert mem.salience > 0.0
