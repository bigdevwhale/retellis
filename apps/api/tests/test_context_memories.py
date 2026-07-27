"""Phase 2b: the distilled long-term layer (atomic memories) in the context.

``memories_to_message`` renders the top memories as one factual system line;
``build_context`` places it right after the persona block, before the episodic
chains. Honesty contract: facts only, disclosed as distilled from past
conversations — no performed empathy, no fabricated slots when the list is
empty.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ai_companion_contracts import Memory, MemoryStatus

from ai_companion_api.memory import build_context
from ai_companion_api.memory.recall import memories_to_message


def _mem(i: int, content: str, salience: float = 0.5) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=f"m{i}",
        user_id="u",
        persona_id="p",
        content=content,
        tags=[],
        salience=salience,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=now,
        updated_at=now,
    )


def test_memories_to_message_renders_top_n_in_order() -> None:
    mems = [_mem(i, f"fact {i}") for i in range(10)]
    msg = memories_to_message(mems, max_n=3)
    assert msg is not None
    assert msg["role"] == "system"
    # P0 temporal grounding: each timestamped fact carries its relative age.
    assert "fact 0 (today) | fact 1 (today) | fact 2 (today)" in msg["content"]
    assert "fact 3" not in msg["content"]
    # Disclosed as distilled knowledge, not performed feeling.
    assert "distilled from past conversations" in msg["content"]


def test_memories_to_message_dates_relative_to_now() -> None:
    old = _mem(1, "you moved to Berlin")
    old = old.model_copy(update={"created_at": datetime(2026, 1, 15, tzinfo=UTC)})
    msg = memories_to_message([old], now=datetime(2026, 7, 17, tzinfo=UTC))
    assert msg is not None
    assert "you moved to Berlin (6 months ago)" in msg["content"]


def test_memories_to_message_none_on_empty() -> None:
    assert memories_to_message([]) is None
    assert memories_to_message([_mem(1, "   ")]) is None


def test_build_context_places_memories_after_persona_before_chains() -> None:
    memories = {"role": "system", "content": "Facts you have learned about them: dog Maple"}
    chains = [{"role": "system", "content": "What you know so far: they said: Maple died"}]
    msgs = build_context(
        persona_id="aria",
        message="hello",
        salient_chains=chains,
        salient_memories=memories,
    )
    contents = [m["content"] for m in msgs]
    assert contents[1] == memories["content"]
    assert contents[2] == chains[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "hello"}


def test_build_context_without_memories_unchanged() -> None:
    msgs = build_context(persona_id="aria", message="hello")
    assert len(msgs) == 2  # persona block + user message only
