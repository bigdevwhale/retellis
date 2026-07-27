"""P0 long-term-conversation upgrades.

Covers the four P0 mechanics:
- ``rank_memories`` — context slots split between the stable identity core
  (top salience) and rows relevant to *this* message (P0 #1).
- temporal grounding — relative ages in ``chains_to_messages`` /
  ``memories_to_message`` and the content cap on rendered chains (P0 #2/#5).
- ``factual_novelty`` — the judge parse and the heuristic fallback dimension
  that gates extraction alongside emotional salience (P0 #3).
- ``_session_bridge`` — the first turn of a NEW conversation gets a one-line
  factual bridge from the previous conversation (P0 #4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_companion_contracts import Event, EventChain, EventRole, Memory, MemoryStatus

from ai_companion_api.memory.event_chain import append_event
from ai_companion_api.memory.recall import (
    chains_to_messages,
    memories_to_message,
    rank_memories,
    relative_time,
)
from ai_companion_api.memory.salience import score_salience
from ai_companion_api.memory.salience_llm import _parse
from ai_companion_api.memory.session_bridge import build_session_bridge as _session_bridge
from ai_companion_api.memory.store import InMemoryStore

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _mem(i: int, content: str, salience: float, *, age_days: int = 0) -> Memory:
    ts = NOW - timedelta(days=age_days)
    return Memory(
        id=f"m{i}",
        user_id="u",
        persona_id="p",
        content=content,
        tags=[],
        salience=salience,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=ts,
        updated_at=ts,
    )


def _event(i: int, content: str, *, age_days: int = 0, prev: str | None = None) -> Event:
    return Event(
        id=f"e{i}",
        user_id="u",
        persona_id="p",
        prev_event_id=prev,
        role=EventRole.user,
        content=content,
        salience=0.5,
        created_at=NOW - timedelta(days=age_days),
    )


# --- P0 #1: relevance-aware memory selection ---------------------------------


def test_rank_memories_surfaces_relevant_row_past_the_salience_top() -> None:
    # 10 heavyweight memories occupy the salience top; the dog fact sits at
    # the bottom by salience but is exactly what the query asks about.
    mems = [_mem(i, f"major life event number {i}", 0.9) for i in range(10)]
    mems.append(_mem(99, "the name of your dog is Maple", 0.2))
    picked = rank_memories(mems, "what is the name of my dog Maple", now=NOW)
    contents = [m.content for m in picked]
    assert "the name of your dog is Maple" in contents
    # Stable slots keep the caller's salience order.
    assert contents[0] == "major life event number 0"
    assert len(picked) == 6


def test_rank_memories_small_set_passthrough() -> None:
    mems = [_mem(i, f"fact {i}", 0.5) for i in range(4)]
    assert rank_memories(mems, "anything", now=NOW) == mems


# --- P0 #2/#5: temporal grounding + chain content cap -------------------------


def test_relative_time_buckets() -> None:
    assert relative_time(NOW, NOW) == "today"
    assert relative_time(NOW - timedelta(days=1), NOW) == "yesterday"
    assert relative_time(NOW - timedelta(days=5), NOW) == "5 days ago"
    assert relative_time(NOW - timedelta(days=30), NOW) == "4 weeks ago"
    assert relative_time(NOW - timedelta(days=120), NOW) == "3 months ago"
    assert relative_time(NOW - timedelta(days=900), NOW) == "2 years ago"


def test_chains_render_relative_age_and_cap_content() -> None:
    long_tail = "x" * 1000
    chain = EventChain(
        events=[_event(1, "my dad died", age_days=95), _event(2, long_tail, age_days=95)],
        salience_sum=1.0,
    )
    msgs = chains_to_messages([chain], now=NOW)
    assert len(msgs) == 1
    assert msgs[0]["content"].startswith("What you know so far (3 months ago):")
    # Capped: the 1000-char event renders at most 300 chars of content.
    assert "x" * 301 not in msgs[0]["content"]
    assert "x" * 100 in msgs[0]["content"]


def test_chains_without_timestamps_render_as_before() -> None:
    ev = _event(1, "hello").model_copy(update={"created_at": None})
    msgs = chains_to_messages([EventChain(events=[ev], salience_sum=0.5)])
    assert msgs[0]["content"].startswith("What you know so far: ")


def test_memories_without_timestamps_render_as_before() -> None:
    m = _mem(1, "plain fact", 0.5).model_copy(update={"created_at": None})
    msg = memories_to_message([m])
    assert msg is not None
    assert "plain fact" in msg["content"]
    assert "(today)" not in msg["content"]


# --- P0 #3: factual_novelty ---------------------------------------------------


def test_judge_parse_reads_factual_novelty_and_defaults_missing() -> None:
    parsed = _parse(
        '{"salience": 0.1, "short_term_salience": 0.1, "emotional_intensity": 0.0,'
        ' "factual_novelty": 0.8, "emotion_tags": []}'
    )
    assert parsed is not None
    assert parsed.factual_novelty == 0.8
    # Old-shape replies (pre-P0) still parse; the dimension defaults to 0.
    legacy = _parse(
        '{"salience": 0.5, "short_term_salience": 0.5, "emotional_intensity": 0.5,'
        ' "emotion_tags": ["sad"]}'
    )
    assert legacy is not None
    assert legacy.factual_novelty == 0.0


def test_heuristic_factual_novelty_fires_on_anchored_facts() -> None:
    factual = score_salience("I got a new job at the hospital, I start on May 12")
    chitchat = score_salience("haha yeah totally")
    assert factual.factual_novelty > chitchat.factual_novelty
    assert factual.factual_novelty >= 0.3


# --- P0 #4: session bridge ----------------------------------------------------


async def test_session_bridge_summarizes_previous_convo() -> None:
    store = InMemoryStore()
    for role, text in [
        (EventRole.user, "I have my job interview on Friday"),
        (EventRole.assistant, "Good luck — you prepared well."),
    ]:
        await append_event(
            store,
            user_id="u",
            persona_id="aria",
            convo_id="c-old",
            role=role,
            content=text,
        )
    bridge = await _session_bridge(
        store,
        user_id="u",
        persona_id="aria",
        convo_id="c-new",
        family_id=None,
        visibility=None,
        participant_user_id=None,
        family_members=None,
    )
    assert bridge is not None
    assert bridge["role"] == "system"
    assert bridge["content"].startswith("Your previous conversation with them (today)")
    assert "job interview on Friday" in bridge["content"]
    assert "do not force it" in bridge["content"]


async def test_session_bridge_none_without_prior_convo() -> None:
    store = InMemoryStore()
    bridge = await _session_bridge(
        store,
        user_id="u",
        persona_id="aria",
        convo_id="c-first",
        family_id=None,
        visibility=None,
        participant_user_id=None,
        family_members=None,
    )
    assert bridge is None


async def test_session_bridge_scoped_to_other_users_is_empty() -> None:
    store = InMemoryStore()
    await append_event(
        store,
        user_id="someone-else",
        persona_id="aria",
        convo_id="c-old",
        role=EventRole.user,
        content="private stuff",
    )
    bridge = await _session_bridge(
        store,
        user_id="u",
        persona_id="aria",
        convo_id="c-new",
        family_id=None,
        visibility=None,
        participant_user_id=None,
        family_members=None,
    )
    assert bridge is None
