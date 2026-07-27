"""P2 long-term-conversation upgrades.

Covers:
- utility-model selection + utility-usage accounting on the adapter (P2-2);
- memory salience decay in the relevant slots + superseded episodes reachable
  via ``list_memories(include_superseded=True)`` (P2-3);
- language-aware heuristic fallback — non-Latin text no longer scores ~0.1
  across the board (P2-4).

The memory-probe eval itself lives in ``packages/eval`` (deterministic CI
gate); these are the unit-level counterparts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_companion_contracts import Memory, MemoryStatus

from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter
from ai_companion_api.llm.provider import utility_model_for
from ai_companion_api.memory.recall import (
    effective_memory_salience,
    rank_memories,
)
from ai_companion_api.memory.salience import score_salience
from ai_companion_api.memory.store import InMemoryStore

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _mem(
    mid: str,
    content: str,
    salience: float,
    *,
    age_days: float = 0,
    status: MemoryStatus = MemoryStatus.active,
    tags: list[str] | None = None,
) -> Memory:
    ts = NOW - timedelta(days=age_days)
    return Memory(
        id=mid,
        user_id="u",
        persona_id="p",
        content=content,
        tags=tags or [],
        salience=salience,
        source_event_ids=[],
        status=status,
        created_at=ts,
        updated_at=ts,
    )


# --- P2-2: utility model + metering -------------------------------------------


def test_utility_model_for_maps_kind_and_honors_override() -> None:
    assert utility_model_for("openai", "gpt-4o") == "gpt-4o-mini"
    assert utility_model_for("anthropic", "claude-opus-4") == "claude-3-5-haiku-latest"
    # No known cheap sibling → serving model (the ollama tag must exist locally).
    assert utility_model_for("ollama", "ollama/llama3.3") == "ollama/llama3.3"
    # Operator override wins for every kind.
    assert utility_model_for("openai", "gpt-4o", override="my-model") == "my-model"


def test_adapter_accumulates_and_drains_utility_usage() -> None:
    adapter = LiteLLMAdapter("openai", "k")

    class _U:
        prompt_tokens = 100
        completion_tokens = 20

    adapter._track_utility("gpt-4o-mini", _U())
    adapter._track_utility("gpt-4o-mini", _U())
    adapter._track_utility("gpt-4o", _U())
    rows = adapter.drain_utility_usage()
    by_model = {r.model: r for r in rows}
    assert by_model["gpt-4o-mini"].prompt_tokens == 200
    assert by_model["gpt-4o-mini"].completion_tokens == 40
    assert by_model["gpt-4o-mini"].cost_usd > 0
    assert by_model["gpt-4o"].prompt_tokens == 100
    # Drain resets — a second drain is empty (no double metering).
    assert adapter.drain_utility_usage() == []


def test_track_utility_ignores_empty_usage() -> None:
    adapter = LiteLLMAdapter("openai", "k")
    adapter._track_utility("gpt-4o-mini", None)
    assert adapter.drain_utility_usage() == []


# --- P2-3: memory decay + superseded episodes ----------------------------------


def test_effective_memory_salience_decays_with_floor() -> None:
    fresh = _mem("a", "x", 0.8, age_days=0)
    old = _mem("b", "x", 0.8, age_days=120)  # one half-life
    ancient = _mem("c", "x", 0.8, age_days=3650)
    assert effective_memory_salience(fresh, NOW) == 0.8
    assert abs(effective_memory_salience(old, NOW) - 0.4) < 0.01
    # Floor: never below 0.25 of stored salience.
    assert abs(effective_memory_salience(ancient, NOW) - 0.8 * 0.25) < 1e-9
    # No timestamp → no decay (synthetic/eval rows).
    bare = _mem("d", "x", 0.8).model_copy(update={"updated_at": None})
    assert effective_memory_salience(bare, NOW) == 0.8


def test_rank_memories_old_relevant_fact_beats_fresh_heavyweights() -> None:
    # The memory-probe scenario in miniature: 10 fresh heavyweight fillers vs
    # a 200-day-old low-salience fact the query is exactly about.
    mems = [_mem(f"f{i}", f"major life event number {i}", 0.9, age_days=30) for i in range(10)]
    mems.append(_mem("dog", "the name of your dog is Maple", 0.2, age_days=200))
    picked = rank_memories(mems, "what is the name of my dog maple", now=NOW)
    assert any(m.id == "dog" for m in picked)


async def test_list_memories_include_superseded() -> None:
    store = InMemoryStore()
    await store.add_memory(_mem("act", "active fact", 0.5))
    await store.add_memory(
        _mem("sup", "superseded episode", 0.5, status=MemoryStatus.superseded, tags=["episode"])
    )
    active_only = await store.list_memories(user_id="u", persona_id="p")
    assert [m.id for m in active_only] == ["act"]
    widened = await store.list_memories(user_id="u", persona_id="p", include_superseded=True)
    assert {m.id for m in widened} == {"act", "sup"}


# --- P2-4: language-aware heuristic --------------------------------------------


def test_russian_text_no_longer_scores_flat_zero() -> None:
    ru = score_salience(
        "кстати, я вчера переехал в Берлин и начал искать работу в новой компании, "
        "это большое изменение в моей жизни и я много об этом думаю"
    )
    assert ru.salience >= 0.25
    assert ru.factual_novelty >= 0.25
    # The heuristic cannot SEE emotion in a language it has no lexicon for —
    # it must not fabricate intensity ("disclose, don't perform").
    assert ru.emotional_intensity <= 0.15
    assert ru.emotion_tags == []


def test_english_heuristic_unchanged_by_language_patch() -> None:
    en = score_salience("I am really sad, my dog died yesterday")
    assert en.emotional_intensity > 0.4
    assert "sad" in (en.emotion_tags or [])
    # Short RU chitchat still scores low — the patch scales with length.
    assert score_salience("да, наверное").salience < 0.2
