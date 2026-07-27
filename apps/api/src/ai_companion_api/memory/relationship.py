"""Relationship note — the slowly-evolving carrier of "we have history" (P1).

The persona block is deterministic by design (anti-drift), so *development over
time* must live somewhere else. This module maintains ONE small factual note
per (user, persona, scope) — how long the companion has known the user, the
threads that persist across months, learned communication preferences ("keep
replies short"), standing agreements — stored as a ``Memory`` row tagged
``relationship-note`` and injected right after the persona block.

Cadence: regenerated only when episodic consolidation just fired (roughly once
per ``CONSOLIDATE_MIN_UNCOVERED`` turns per convo), so it costs one extra LLM
call per ~20+ turns, never per turn. The note is rebuilt from the memory layer
(distilled facts + episode/era summaries + the previous note), not from itself
alone — bounded self-reinforcement.

Invariants (same family as consolidation):
- Never raises; any failure returns ``None`` and the old note stays active.
- The old note is superseded AFTER the new one exists (crash-safe order).
- "Disclose, don't perform": facts about the relationship, never claimed
  feelings; the prompt forbids empathetic language and demands JSON only.
- The extractor never sees or edits this row (filtered out in ``extract.py``);
  only this pass rewrites it.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_companion_contracts import Memory, MemoryStatus

from ..observability import redact
from .store import MemoryStore

if TYPE_CHECKING:
    from ..llm.types import LlmAdapter

logger = logging.getLogger(__name__)

NOTE_TAG = "relationship-note"

# Feed the note-writer a bounded view of the distilled layer.
_MAX_MEMORIES = 16
_MAX_MEMORY_TEXT = 300

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

_NOTE_SYSTEM = (
    "You are a relationship-context module for an AI companion app. You receive "
    "today's date, the companion's previous relationship note (may be empty), and "
    "dated distilled memories about one person. Write ONE compact note the "
    "companion will silently read before every reply. Output ONLY a JSON object: "
    '{"note": "<3-5 short sentences>"}.\n'
    "- Cover only what helps continuity: how long you have known them (from the "
    "earliest dates), the few threads that persist across months, their stated "
    "communication preferences ('keep replies short'), standing agreements.\n"
    "- Address the companion: 'You have known them since April 2026. They prefer "
    "…'. Concrete, past/present tense, no speculation.\n"
    "- LANGUAGE: write the note in the SAME language the memories are written in. "
    "Do NOT translate to English.\n"
    "- Do NOT invent facts or dates not present in the input. Do NOT describe "
    "feelings the companion has (it has none to describe). No advice, no plans.\n"
    "- Output JSON only — no prose, no markdown, no code fences."
)


async def maybe_update_relationship_note(
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
    """Regenerate the relationship note from the current distilled layer.

    Called by the router only when consolidation just produced a new episode
    (the cadence gate lives at the call site). Returns the new ``Memory`` or
    ``None`` (mock / failure / nothing to build from). Never raises."""
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
        prev_note = next((m for m in memories if NOTE_TAG in m.tags), None)
        rows = [m for m in memories if NOTE_TAG not in m.tags]
        if not rows:
            return None
        # Oldest first so the earliest dates (relationship start) survive the cap.
        rows.sort(key=lambda m: m.created_at)
        listing = "\n".join(
            f"- [{m.created_at:%Y-%m-%d}] {m.content[:_MAX_MEMORY_TEXT]}"
            for m in rows[:_MAX_MEMORIES]
        )
        payload = (
            f"today: {datetime.now(UTC):%Y-%m-%d}\n"
            f"previous_note: {prev_note.content if prev_note else '(none)'}\n"
            f"memories:\n{listing}"
        )
        raw = await adapter.complete(
            [
                {"role": "system", "content": _NOTE_SYSTEM},
                {"role": "user", "content": payload},
            ],
            model,
        )
        note_text = _parse(raw)
        if not note_text:
            logger.debug(
                "relationship note reply unparseable (kept previous): %s",
                redact((raw or "")[:200]),
            )
            return None
        now = datetime.now(UTC)
        note = Memory(
            id=uuid.uuid4().hex,
            user_id=user_id,
            persona_id=persona_id,
            content=note_text,
            tags=[NOTE_TAG],
            salience=0.8,
            source_event_ids=[],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        await store.add_memory(note)
        # Supersede the previous note AFTER the new one exists — a crash here
        # leaves two active notes (the injector picks the newest), never zero.
        if prev_note is not None:
            await store.supersede_memory(
                memory_id=prev_note.id,
                user_id=user_id,
                persona_id=persona_id,
                superseded_by=note.id,
                family_id=family_id,
            )
        return note
    except Exception as exc:  # the note must never break a turn
        logger.warning(
            "relationship note update failed (turn continues): %s: %s",
            type(exc).__name__,
            redact(str(exc)),
        )
        return None


def relationship_message(memories: Sequence[object]) -> dict[str, str] | None:
    """Render the newest active relationship note as its own system slot.

    Newest wins — a crash between add and supersede can briefly leave two
    active notes. Lives here (not in the router) so the eval gate can probe
    the full context assembly litellm-/fastapi-free."""
    notes = [m for m in memories if NOTE_TAG in (getattr(m, "tags", None) or [])]
    if not notes:
        return None
    newest = max(notes, key=lambda m: getattr(m, "created_at", datetime.min.replace(tzinfo=UTC)))
    content = str(getattr(newest, "content", "")).strip()
    if not content:
        return None
    return {
        "role": "system",
        "content": f"Relationship context (distilled from your history together): {content}",
    }


def _parse(raw: str) -> str | None:
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
    note = str(obj.get("note", "")).strip()
    return note or None


__all__ = ["NOTE_TAG", "maybe_update_relationship_note", "relationship_message"]
