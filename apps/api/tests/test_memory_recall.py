"""Event-chain memory: append + linkage, salience, recall ranking, chain walks.

Unit-level (no HTTP) against ``InMemoryStore`` — the same store the app uses by
default. Exercises the full embed → rank → chain pipeline deterministically.
"""

from __future__ import annotations

import pytest
from ai_companion_contracts import EventRole

from ai_companion_api.memory import (
    InMemoryStore,
    append_event,
    chains_to_messages,
    rank_and_chain,
    score_salience,
)

USER = "u1"
PERSONA = "aria"
CONVO = "c1"


@pytest.mark.asyncio
async def test_append_links_events_into_a_chain() -> None:
    store = InMemoryStore()
    u = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    a = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.assistant,
        content="I'm sorry. What was Maple like?",
    )
    assert a.prev_event_id == u.id  # assistant links to the user event
    events = await store.list_events(user_id=USER, persona_id=PERSONA)
    assert [e.id for e in events] == [u.id, a.id]


@pytest.mark.asyncio
async def test_recall_surfaces_seeded_event_for_probe() -> None:
    store = InMemoryStore()
    await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    chains = await store.recall_chains(
        user_id=USER, persona_id=PERSONA, query="What was the name of my dog?"
    )
    assert chains, "expected at least one recalled chain"
    blob = " ".join(e.content for ch in chains for e in ch.events)
    assert "Maple" in blob


@pytest.mark.asyncio
async def test_recall_returns_empty_for_unrelated_query_when_store_empty() -> None:
    store = InMemoryStore()
    chains = await store.recall_chains(user_id=USER, persona_id=PERSONA, query="anything")
    assert chains == []


@pytest.mark.asyncio
async def test_rank_and_chain_walks_prev_event_link() -> None:
    store = InMemoryStore()
    u = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    a = await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.assistant,
        content="I'm sorry. What was Maple like?",
    )
    cands = await store.recall_candidates(user_id=USER, persona_id=PERSONA)
    chains = rank_and_chain(cands, "What was the name of my dog?", k=3)
    assert chains
    seed_chain = chains[0]
    ids = {e.id for e in seed_chain.events}
    # The chain should include the seed and walk back to its predecessor.
    assert a.id in ids or u.id in ids
    # Predecessor (if present) must be linked via prev_event_id.
    for i in range(1, len(seed_chain.events)):
        assert seed_chain.events[i].prev_event_id == seed_chain.events[i - 1].id


def test_salience_orders_emotional_over_neutral() -> None:
    emo = score_salience("My dog Maple died last Tuesday.")
    neutral = score_salience("The weather is nice today, maybe a walk.")
    assert emo.salience > neutral.salience
    assert emo.emotional_intensity > neutral.emotional_intensity
    for s in (emo, neutral):
        assert 0.0 <= s.salience <= 1.0
        assert 0.0 <= s.short_term_salience <= 1.0
        assert 0.0 <= s.emotional_intensity <= 1.0


@pytest.mark.asyncio
async def test_chains_to_messages_is_factual_no_performed_empathy() -> None:
    store = InMemoryStore()
    await append_event(
        store,
        user_id=USER,
        persona_id=PERSONA,
        convo_id=CONVO,
        role=EventRole.user,
        content="My dog Maple died last Tuesday.",
    )
    chains = await store.recall_chains(user_id=USER, persona_id=PERSONA, query="dog")
    msgs = chains_to_messages(chains)
    assert msgs, "expected at least one rendered chain message"
    blob = " ".join(m["content"] for m in msgs).lower()
    assert "what you know so far" in blob  # factual framing
    # No performed-empathy phrases leak into the recall block.
    assert "i feel your pain" not in blob
    assert "my heart goes out" not in blob
