"""P1 long-term-conversation upgrades.

Covers:
- forward chain walk — a salient seed carries its aftermath (P1-A);
- attributed extraction — family turns send a ``speaker`` field and the
  relevance-selected existing set excludes the relationship note (P1-B);
- relationship note — generated from the distilled layer, previous note
  superseded crash-safely (P1-C);
- attributed consolidation transcript (P1-D);
- open-loops rendering — fresh loops surface (max 2), stale ones don't (P1-E).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from ai_companion_contracts import Event, EventRole, Memory, MemoryStatus

from ai_companion_api.memory.consolidate import maybe_consolidate
from ai_companion_api.memory.event_chain import append_event
from ai_companion_api.memory.extract import extract_memories
from ai_companion_api.memory.recall import (
    open_loops_message as _open_loops_message,
)
from ai_companion_api.memory.recall import (
    rank_and_chain,
)
from ai_companion_api.memory.relationship import (
    NOTE_TAG,
    maybe_update_relationship_note,
)
from ai_companion_api.memory.relationship import (
    relationship_message as _relationship_message,
)
from ai_companion_api.memory.store import InMemoryStore

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


class FakeAdapter:
    """Records the prompt it was given and returns a canned reply."""

    provider_kind = "openai"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.calls.append(messages)
        return self.reply


def _event(
    i: int,
    content: str,
    *,
    prev: str | None = None,
    role: EventRole = EventRole.user,
    participant: str | None = None,
) -> Event:
    return Event(
        id=f"e{i}",
        user_id="u",
        persona_id="p",
        prev_event_id=prev,
        role=role,
        content=content,
        salience=0.5,
        created_at=NOW,
        participant_user_id=participant,
    )


def _mem(
    i: int, content: str, tags: list[str], *, age_days: int = 0, base: datetime = NOW
) -> Memory:
    ts = base - timedelta(days=age_days)
    return Memory(
        id=f"m{i}",
        user_id="u",
        persona_id="p",
        content=content,
        tags=tags,
        salience=0.5,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=ts,
        updated_at=ts,
    )


# --- P1-A: forward chain walk --------------------------------------------------


def test_chain_walk_includes_aftermath() -> None:
    e1 = _event(1, "small talk before")
    e2 = _event(2, "my dad died yesterday", prev="e1")
    e3 = _event(3, "the aftermath reply", prev="e2", role=EventRole.assistant)
    chains = rank_and_chain([e1, e2, e3], "my dad died", k=1, now=NOW)
    assert len(chains) == 1
    ids = [e.id for e in chains[0].events]
    assert ids == ["e1", "e2", "e3"]  # backward context + seed + forward aftermath


def test_chain_walk_forward_does_not_steal_used_events() -> None:
    # Two seeds where the second seed's child is already used by chain one.
    e1 = _event(1, "my dog maple is sick", prev=None)
    e2 = _event(2, "my dog maple got better", prev="e1")
    chains = rank_and_chain([e1, e2], "dog maple", k=2, now=NOW)
    all_ids = [e.id for ch in chains for e in ch.events]
    assert sorted(all_ids) == ["e1", "e2"]  # no duplicates across chains


# --- P1-B: attributed extraction + relevance pool ------------------------------


async def test_extract_sends_speaker_for_family_events() -> None:
    adapter = FakeAdapter("[]")
    events = [
        _event(1, "I have been stressed at work", participant="alex-id"),
        _event(2, "school is fine", participant="kid-id"),
    ]
    await extract_memories(
        adapter,
        "gpt-x",
        recent_events=events,
        existing_memories=[],
        new_user_event_id="e2",
        participants={"alex-id": "Alex (parent)", "kid-id": "Sam (child)"},
    )
    payload = json.loads(adapter.calls[0][1]["content"])
    speakers = [row.get("speaker") for row in payload["recent_events"]]
    assert speakers == ["Alex (parent)", "Sam (child)"]
    assert "SPEAKERS" in adapter.calls[0][0]["content"]
    assert "open_loop" in adapter.calls[0][0]["content"]


async def test_extract_existing_pool_excludes_relationship_note() -> None:
    adapter = FakeAdapter("[]")
    existing = [_mem(1, "you like tea", ["habits"]), _mem(2, "known since spring", [NOTE_TAG])]
    await extract_memories(
        adapter,
        "gpt-x",
        recent_events=[_event(1, "hello")],
        existing_memories=existing,
        new_user_event_id="e1",
    )
    payload = json.loads(adapter.calls[0][1]["content"])
    ids = [m["id"] for m in payload["existing_memories"]]
    assert ids == ["m1"]


# --- P1-C: relationship note ----------------------------------------------------


async def test_relationship_note_created_and_superseded() -> None:
    store = InMemoryStore()
    await store.add_memory(_mem(1, "you moved to Berlin", ["move"], age_days=90))
    adapter = FakeAdapter('{"note": "You have known them since April 2026."}')
    note1 = await maybe_update_relationship_note(
        adapter, "gpt-x", store, user_id="u", persona_id="p"
    )
    assert note1 is not None and NOTE_TAG in note1.tags
    # The dated distilled layer reached the prompt.
    assert "you moved to Berlin" in adapter.calls[0][1]["content"]
    assert "2026-04-18" in adapter.calls[0][1]["content"]  # 90 days before NOW-ish date
    # Second regeneration supersedes the first — exactly one active note.
    note2 = await maybe_update_relationship_note(
        adapter, "gpt-x", store, user_id="u", persona_id="p"
    )
    assert note2 is not None
    active = await store.list_memories(user_id="u", persona_id="p")
    notes = [m for m in active if NOTE_TAG in m.tags]
    assert [m.id for m in notes] == [note2.id]


async def test_relationship_note_mock_and_empty_noop() -> None:
    store = InMemoryStore()

    class MockAdapter:
        provider_kind = "mock"

    assert (
        await maybe_update_relationship_note(
            MockAdapter(), "mock", store, user_id="u", persona_id="p"
        )
        is None
    )
    # No memories at all → nothing to build from.
    adapter = FakeAdapter('{"note": "x"}')
    assert (
        await maybe_update_relationship_note(adapter, "m", store, user_id="u", persona_id="p")
        is None
    )


def test_relationship_message_picks_newest_note() -> None:
    old = _mem(1, "old note", [NOTE_TAG], age_days=30)
    new = _mem(2, "new note", [NOTE_TAG], age_days=1)
    msg = _relationship_message([old, new])
    assert msg is not None
    assert "new note" in msg["content"]
    assert "Relationship context" in msg["content"]
    assert _relationship_message([_mem(3, "plain", ["x"])]) is None


# --- P1-D: attributed consolidation ---------------------------------------------


async def test_consolidation_transcript_carries_speaker_names() -> None:
    store = InMemoryStore()
    for i in range(40):
        await append_event(
            store,
            user_id="u",
            persona_id="p",
            convo_id="c1",
            role=EventRole.user if i % 2 == 0 else EventRole.assistant,
            content=f"line {i}",
            participant_user_id="alex-id" if i % 2 == 0 else None,
        )
    adapter = FakeAdapter('{"summary": "Alex talked.", "tags": ["family"], "salience": 0.5}')
    episode = await maybe_consolidate(
        adapter,
        "gpt-x",
        store,
        user_id="u",
        persona_id="p",
        convo_id="c1",
        family_members={"alex-id": "Alex (parent)"},
    )
    assert episode is not None
    transcript = adapter.calls[0][1]["content"]
    assert "Alex (parent): line 0" in transcript
    assert "SPEAKERS" in adapter.calls[0][0]["content"]


# --- P1-E: open loops rendering ---------------------------------------------------


def test_open_loops_renders_fresh_max_two_and_skips_stale() -> None:
    # ``_open_loops_message`` measures staleness against the real clock —
    # build ages relative to it, not the fixed test NOW.
    wall = datetime.now(UTC)
    loops = [
        _mem(1, "Job interview on Friday", ["open_loop", "работа"], age_days=2, base=wall),
        _mem(2, "Waiting for test results", ["open_loop"], age_days=5, base=wall),
        _mem(3, "Old plan to move", ["open_loop"], age_days=60, base=wall),  # stale
        _mem(4, "Third fresh loop", ["open_loop"], age_days=1, base=wall),
    ]
    msg = _open_loops_message(loops)
    assert msg is not None
    assert "Old plan to move" not in msg["content"]
    # Max 2, most recently updated first.
    assert "Third fresh loop" in msg["content"]
    assert "Job interview on Friday" in msg["content"]
    assert "Waiting for test results" not in msg["content"]
    assert "only if" in msg["content"]  # never nagging
    assert _open_loops_message([_mem(9, "no loops here", ["x"])]) is None
