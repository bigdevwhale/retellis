"""Episodic consolidation — old event stretches compress into episode memories.

Phase 2c of the long-term-conversation architecture. Over months a conversation
accumulates hundreds of raw events; the recall window and the salience decay
keep the prompt bounded, but months-old *narrative* ("that spring you changed
jobs and were anxious for weeks") would be unreachable. ``maybe_consolidate``
watches a convo and, when enough OLD events are not yet covered by an episode
summary, asks the serving model to compress the oldest uncovered batch into ONE
``Memory`` row tagged ``episode`` (with full provenance in
``source_event_ids``). Episode memories flow through the existing distilled
layer (``memories_to_message`` → the context's ``salient_memories`` slot) and
the /memory page, so nothing new is needed downstream.

Invariants:
- Never raises; returns ``None`` on any failure (mock adapter, LLM error,
  unparseable reply, below threshold) — consolidation must never break a turn.
- Raw events are NOT deleted — the chain stays the recall substrate; the
  episode memory is a synthesized view on top (same contract as extraction).
- The freshest ``RECENT_KEEP`` events are never consolidated (still live).
- Coverage is tracked via ``source_event_ids`` of ``episode``-tagged memories,
  so re-runs are naturally idempotent once a batch is covered.
- "Disclose, don't perform": the summarizer classifies and summarizes; the
  system prompt forbids empathetic language and demands JSON only.

Cost: one extra LLM call roughly every ``CONSOLIDATE_MIN_UNCOVERED`` turns per
convo, on the user's own key/model (inside the BYOK zeroize window).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_companion_contracts import Memory, MemoryStatus

from ..observability import redact
from .store import MemoryStore

if TYPE_CHECKING:
    from ..llm.types import LlmAdapter

logger = logging.getLogger(__name__)

# Don't summarize until this many old events are uncovered; then summarize at
# most CONSOLIDATE_BATCH of the oldest. The freshest RECENT_KEEP events are
# always left alone (they are still live context).
CONSOLIDATE_MIN_UNCOVERED = 20
CONSOLIDATE_BATCH = 30
RECENT_KEEP = 12

# Second consolidation tier (Phase 3b): once this many episode memories have
# accumulated for a persona, the oldest ERA_BATCH compress into ONE era memory
# and the constituent episodes are superseded by it (retrievable, not shown).
ERA_MIN_EPISODES = 8
ERA_BATCH = 12

# Honest limit — one bounded scan per turn, same style as _CONVO_SCAN_LIMIT.
_SCAN_LIMIT = 1000
_MAX_EVENT_TEXT = 400

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

_ERA_SYSTEM = (
    "You are a memory consolidation module for an AI companion app. You receive a "
    "chronological list of EPISODE SUMMARIES about one person, spanning weeks or "
    "months. Compress them into ONE higher-level era summary. Output ONLY a JSON "
    'object: {"summary": "<3-5 sentences>", "tags": [<strings>], "salience": <0..1>}.\n'
    "- summary: the arc of this period — what changed, what persisted, what "
    "mattered — in second person toward the user ('You …'), past tense, concrete. "
    "Keep names and pivotal events; drop day-to-day detail.\n"
    "- TIME: episode lines may begin with a [YYYY-MM-DD] date. Anchor the summary in "
    "that period (e.g. 'In the spring of 2026 …', 'Between March and June …'). Use "
    "only the dates provided; never invent dates.\n"
    "- LANGUAGE: write summary and tags in the SAME language the episodes are "
    "written in. Do NOT translate to English.\n"
    "- tags: 1-4 lowercase single-word themes.\n"
    "- salience: 0..1 — how much this era should matter years later.\n"
    "- Do NOT empathize, acknowledge, or perform feelings. Output JSON only — no "
    "prose, no markdown, no code fences."
)

_SUMMARY_SYSTEM = (
    "You are a memory consolidation module for an AI companion app. You receive a "
    "chronological transcript excerpt from ONE conversation. Compress it into ONE "
    'episode summary. Output ONLY a JSON object: {"summary": "<2-4 sentences>", '
    '"tags": [<strings>], "salience": <0..1>}.\n'
    "- summary: what happened and what mattered, in second person toward the user "
    "('You …'), past tense, concrete (names, events, decisions). No greetings, no "
    "filler.\n"
    "- TIME: transcript lines may begin with a [YYYY-MM-DD] date. Anchor the summary "
    "in time naturally (e.g. 'In April 2026 you …'). Use only the dates provided; "
    "never invent dates.\n"
    "- SPEAKERS: user lines may be attributed to named participants (family sessions, "
    "e.g. 'Alex (parent): …'). When they are, write the summary in THIRD person using "
    "those names and never attribute one participant's words to another.\n"
    "- LANGUAGE: write summary and tags in the SAME language the user wrote in — "
    "detect it from the transcript. Do NOT translate to English.\n"
    "- tags: 1-4 lowercase single-word themes.\n"
    "- salience: 0..1 — how much this episode should matter months later.\n"
    "- Do NOT empathize, acknowledge, or perform feelings. Output JSON only — no "
    "prose, no markdown, no code fences."
)


async def maybe_consolidate(
    adapter: LlmAdapter,
    model: str,
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    convo_id: str,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
    family_members: dict[str, str] | None = None,
) -> Memory | None:
    """Consolidate the oldest uncovered event stretch of ``convo_id`` into an
    episode memory, if the threshold is met. Returns the new ``Memory`` or
    ``None`` (below threshold / mock / failure). Never raises.

    ``family_members`` (P1) maps ``user_id → display name`` so multi-speaker
    family sessions consolidate into attributed third-person summaries instead
    of conflating participants under one "user"."""
    try:
        if getattr(adapter, "provider_kind", "") == "mock" or not hasattr(adapter, "complete"):
            return None
        events = await store.list_events(
            user_id=user_id,
            persona_id=persona_id,
            limit=_SCAN_LIMIT,
            convo_id=convo_id,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        if len(events) <= RECENT_KEEP:
            return None
        memories = await store.list_memories(
            user_id=user_id,
            persona_id=persona_id,
            include_donors=False,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        covered: set[str] = set()
        for m in memories:
            if "episode" in m.tags:
                covered.update(m.source_event_ids)
        old = events[:-RECENT_KEEP]
        uncovered = [e for e in old if e.id not in covered]
        if len(uncovered) < CONSOLIDATE_MIN_UNCOVERED:
            return None
        batch = uncovered[:CONSOLIDATE_BATCH]

        fm = family_members or {}

        def _speaker(e) -> str:  # type: ignore[no-untyped-def]
            role = e.role.value if hasattr(e.role, "value") else str(e.role)
            pid = getattr(e, "participant_user_id", None)
            if role == "user" and pid and pid in fm:
                return fm[pid]
            return "user" if role == "user" else "assistant"

        transcript = "\n".join(
            f"{_date_prefix(e.created_at)}{_speaker(e)}: {e.content[:_MAX_EVENT_TEXT]}"
            for e in batch
        )
        raw = await adapter.complete(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            model,
        )
        parsed = _parse(raw)
        if parsed is None:
            logger.debug(
                "consolidation reply unparseable (skipped): %s", redact((raw or "")[:200])
            )
            return None
        summary, tags, salience = parsed
        if salience <= 0.0:
            # Fall back to the strongest constituent event — an episode is at
            # least as salient as its peak moment, discounted a little.
            salience = max((float(e.salience) for e in batch), default=0.3) * 0.9
        now = datetime.now(UTC)
        memory = Memory(
            id=uuid.uuid4().hex,
            user_id=user_id,
            persona_id=persona_id,
            content=summary,
            tags=["episode", *tags[:4]],
            salience=max(0.0, min(1.0, salience)),
            source_event_ids=[e.id for e in batch],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        await store.add_memory(memory)
        return memory
    except Exception as exc:  # consolidation must never break a turn
        logger.warning(
            "consolidation failed (turn continues): %s: %s", type(exc).__name__, redact(str(exc))
        )
        return None


async def maybe_consolidate_eras(
    adapter: LlmAdapter,
    model: str,
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
) -> Memory | None:
    """Second tier (Phase 3b): compress accumulated episode memories into one
    era memory. The constituent episodes are superseded by the era (they stay
    in the store for provenance but leave the active set, so the distilled
    context layer and /memory show the era instead of a dozen episodes).
    Cross-convo by construction — episodes carry no convo boundary. Returns
    the era ``Memory`` or ``None`` (below threshold / mock / failure). Never
    raises."""
    try:
        if getattr(adapter, "provider_kind", "") == "mock" or not hasattr(adapter, "complete"):
            return None
        memories = await store.list_memories(
            user_id=user_id,
            persona_id=persona_id,
            include_donors=False,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        episodes = [m for m in memories if "episode" in m.tags and "era" not in m.tags]
        if len(episodes) < ERA_MIN_EPISODES:
            return None
        episodes.sort(key=lambda m: m.created_at)  # oldest era first
        batch = episodes[:ERA_BATCH]

        listing = "\n".join(
            f"- {_date_prefix(m.created_at)}{m.content[:_MAX_EVENT_TEXT]}" for m in batch
        )
        raw = await adapter.complete(
            [
                {"role": "system", "content": _ERA_SYSTEM},
                {"role": "user", "content": listing},
            ],
            model,
        )
        parsed = _parse(raw)
        if parsed is None:
            logger.debug(
                "era consolidation reply unparseable (skipped): %s", redact((raw or "")[:200])
            )
            return None
        summary, tags, salience = parsed
        if salience <= 0.0:
            salience = max((float(m.salience) for m in batch), default=0.3)
        source_ids: list[str] = []
        for m in batch:
            source_ids.extend(m.source_event_ids)
        now = datetime.now(UTC)
        era = Memory(
            id=uuid.uuid4().hex,
            user_id=user_id,
            persona_id=persona_id,
            content=summary,
            tags=["era", *tags[:4]],
            salience=max(0.0, min(1.0, salience)),
            source_event_ids=list(dict.fromkeys(source_ids)),
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        await store.add_memory(era)
        # Supersede the constituents AFTER the era exists, pointing at it —
        # if we crash mid-loop, the worst case is an era plus a few leftover
        # active episodes (redundant but correct), never lost provenance.
        for m in batch:
            await store.supersede_memory(
                memory_id=m.id,
                user_id=user_id,
                persona_id=persona_id,
                superseded_by=era.id,
                family_id=family_id,
            )
        return era
    except Exception as exc:  # consolidation must never break a turn
        logger.warning(
            "era consolidation failed (turn continues): %s: %s",
            type(exc).__name__,
            redact(str(exc)),
        )
        return None


def _date_prefix(dt: datetime | None) -> str:
    """``"[YYYY-MM-DD] "`` for timestamped rows, ``""`` otherwise (P0 #2 —
    without dates in the transcript the model cannot anchor a summary in time,
    and an era covering months reads as one undated blur)."""
    return f"[{dt:%Y-%m-%d}] " if dt is not None else ""


def _parse(raw: str) -> tuple[str, list[str], float] | None:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJECT.search(raw)
        if m is None:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary", "")).strip()
    if not summary:
        return None
    tags_raw = obj.get("tags", []) or []
    tags = (
        [str(t).lower().strip() for t in tags_raw if str(t).strip()]
        if isinstance(tags_raw, list)
        else []
    )
    try:
        salience = float(obj.get("salience", 0.0))
    except (TypeError, ValueError):
        salience = 0.0
    return summary, tags, salience


__all__ = [
    "CONSOLIDATE_BATCH",
    "CONSOLIDATE_MIN_UNCOVERED",
    "ERA_BATCH",
    "ERA_MIN_EPISODES",
    "RECENT_KEEP",
    "maybe_consolidate",
    "maybe_consolidate_eras",
]
