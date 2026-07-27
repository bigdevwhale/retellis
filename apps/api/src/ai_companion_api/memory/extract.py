"""LLM-driven atomic memory extraction — the display layer over the event chain.

After a salient turn, ``extract_memories`` asks the model that served the turn to
look at the recent event window + the persona's existing active memories and
return a JSON array of operations that keep the memory set accurate and
non-redundant:

- ``add``    — a NEW atomic, citable fact not already captured.
- ``update`` — an existing memory needs refining / more tags / a higher salience
  (e.g. it recurred or evolved).
- ``drop``   — an existing memory is contradicted or no longer accurate.

The caller applies the ops to the store. ``None`` (or ``[]``) means "nothing to
change" — used when no real provider served (mock), the call fails, or the reply
is unparseable. Never raises.

"Disclose, don't perform": the extractor *analyzes*, it does not empathize. The
system prompt forbids empathetic language and demands JSON only, so the model
never simulates affect toward the user's messages.

Cost: one extra LLM call per salient turn (gated by the router on judged
salience ≥ threshold), on the user's own key/model — no separate extraction key.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai_companion_contracts import Event, Memory

from ..observability import redact
from .recall import rank_memories

if TYPE_CHECKING:
    from ..llm.types import LlmAdapter

logger = logging.getLogger(__name__)

# Bound the context we feed the extractor — salience is about the gist, and
# existing memories can grow; keep the prompt bounded.
_MAX_RECENT_EVENTS = 8
_MAX_EXISTING_MEMORIES = 40
_MAX_TEXT = 4000

_EXTRACT_SYSTEM = (
    "You are a memory extraction module for an AI companion app. You receive the "
    "user's recent messages and the companion's existing active memories for this "
    "persona. Output ONLY a JSON array of operations that keep the memory set "
    "accurate and non-redundant.\n"
    "Operations:\n"
    '  {"action":"add","content":"<one citable fact>","tags":["..."],"salience":<0..1>,'
    '"source_event_ids":["<ids>"]}  — a NEW fact not already captured.\n'
    '  {"action":"update","id":"<existing memory id>","content":"<refined statement>",'
    '"tags":["..."],"salience":<0..1>,"source_event_ids":["<ids to add>"]}  — an existing '
    "memory needs refining, more tags, or higher salience (it recurred or evolved).\n"
    '  {"action":"drop","id":"<existing memory id>"}  — an existing memory is '
    "contradicted or no longer accurate.\n"
    "Rules:\n"
    "- One memory = one atomic, citable fact ('You have a dog named Maple', NOT 'user "
    "talked about pets'). Write in second person ('You …').\n"
    "- content LANGUAGE: write the memory content in the SAME language the user wrote "
    "their messages in — detect it from recent_events. Do NOT translate to English when "
    "the user wrote in another language (English → 'You have a dog named Maple'; "
    "Russian → «У тебя есть собака по кличке Клен»; etc.). Keep second person in that "
    "language.\n"
    "- tags: 1-3 lowercase single-word themes. LANGUAGE: write the tag WORDS in the SAME "
    "language the user wrote their messages in — detect it from recent_events (English → "
    "work, family, stress, sleep; Russian → работа, семья, стресс, сон; etc.). Do NOT "
    "translate tags to English when the user wrote in another language. REUSE an existing "
    "tag only when it is in that SAME language and means the same thing; otherwise coin a "
    "new tag in the user's language.\n"
    "- salience: 0..1 — how central this is to the user's life right now.\n"
    "- source_event_ids: only ids from the provided recent_events that this memory is "
    "drawn from. For 'update', list only the NEW ids to add.\n"
    "- Do NOT add a memory that duplicates an existing one — update the existing one "
    "instead (bump salience, refine content, extend source_event_ids).\n"
    "- OPEN LOOPS: if the user mentions a specific upcoming or unresolved event (a job "
    "interview on Friday, awaiting test results, a planned move), add it as a memory whose "
    "FIRST tag is exactly 'open_loop' (keep this literal tag in English regardless of the "
    "user's language; content and other tags follow the language rules). When a later "
    "message reveals the outcome, UPDATE that memory: rewrite the content as the resolved "
    "fact and REMOVE the 'open_loop' tag — or drop it if it no longer matters.\n"
    "- SPEAKERS: recent_events may carry a 'speaker' field (family sessions with several "
    "participants). When speakers are present, write facts in THIRD person using the "
    "speaker's name ('Alex (parent) is stressed at work', NOT 'You are stressed'), and "
    "never attribute one speaker's fact to another.\n"
    "- Do NOT empathize, acknowledge, or restate the messages. Do NOT perform empathy. "
    "Output JSON only — no prose, no markdown, no code fences.\n"
    "- If nothing new or changed, output []."
)


@dataclass
class MemoryOp:
    """One extraction operation the router applies to the store."""

    action: str  # "add" | "update" | "drop"
    id: str | None  # existing memory id for update/drop; None for add
    content: str | None = None
    tags: list[str] = field(default_factory=list)
    salience: float = 0.0
    source_event_ids: list[str] = field(default_factory=list)


async def extract_memories(
    adapter: LlmAdapter,
    model: str,
    *,
    recent_events: list[Event],
    existing_memories: list[Memory],
    new_user_event_id: str,
    participants: dict[str, str] | None = None,
) -> list[MemoryOp] | None:
    """Return ops to apply, or ``None`` (caller skips). Never raises.

    ``participants`` (P1, family turns) maps ``user_id → display name`` so
    multi-speaker sessions extract attributed third-person facts instead of an
    ambiguous "You …" that conflates family members.
    """
    if getattr(adapter, "provider_kind", "") == "mock" or not hasattr(adapter, "complete"):
        return None
    if not recent_events:
        return None

    window = recent_events[-_MAX_RECENT_EVENTS:]
    fm = participants or {}

    def _event_row(e: Event) -> dict[str, str]:
        row = {
            "id": e.id,
            "role": e.role.value if hasattr(e.role, "value") else str(e.role),
            "content": e.content[:600],
        }
        pid = getattr(e, "participant_user_id", None)
        if row["role"] == "user" and pid and pid in fm:
            row["speaker"] = fm[pid]
        return row

    # P1: the dedup/contradiction window is selected by RELEVANCE to the
    # current stretch, not by top-salience — with hundreds of active memories
    # the salience top never contains the row a new fact duplicates or
    # contradicts. The relationship note is regenerated by its own pass and is
    # hidden from the extractor so ops can never target it.
    pool = [m for m in existing_memories if "relationship-note" not in m.tags]
    if len(pool) > _MAX_EXISTING_MEMORIES:
        query = " ".join(e.content[:300] for e in window)
        pool = rank_memories(pool, query, k_stable=10, k_relevant=_MAX_EXISTING_MEMORIES - 10)  # type: ignore[assignment]

    payload = {
        "recent_events": [_event_row(e) for e in window],
        "existing_memories": [
            {"id": m.id, "content": m.content, "tags": m.tags}
            for m in pool[:_MAX_EXISTING_MEMORIES]
        ],
        "new_user_event_id": new_user_event_id,
    }
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:_MAX_TEXT]},
    ]
    try:
        raw = await adapter.complete(messages, model)
    except Exception as exc:
        logger.debug(
            "memory extract call failed (skipped): %s: %s", type(exc).__name__, redact(str(exc))
        )
        return None
    ops = _parse_ops(raw)
    if ops is None:
        logger.debug("memory extract reply unparseable (skipped): %s", redact((raw or "")[:200]))
        return None
    return ops


def _parse_ops(raw: str) -> list[MemoryOp] | None:
    if not raw:
        return None
    arr = _to_json_array(raw)
    if arr is None or not isinstance(arr, list):
        return None
    out: list[MemoryOp] = []
    valid_ids = set()  # ids referenced by update/drop — caller validates against store
    for item in arr:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip().lower()
        if action not in {"add", "update", "drop"}:
            continue
        mid = item.get("id")
        if action in {"update", "drop"} and not isinstance(mid, str):
            continue  # update/drop need an existing id
        if action == "drop":
            out.append(MemoryOp(action="drop", id=mid))
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        tags = [str(t).lower().strip() for t in item.get("tags", []) if str(t).strip()][:3]
        try:
            salience = float(item.get("salience", 0.0))
        except (TypeError, ValueError):
            salience = 0.0
        salience = max(0.0, min(1.0, salience))
        src = item.get("source_event_ids", []) or []
        src_ids = [str(s) for s in src if isinstance(s, str) or isinstance(s, int)]
        out.append(
            MemoryOp(
                action=action,
                id=mid if action == "update" else None,
                content=content.strip(),
                tags=tags,
                salience=salience,
                source_event_ids=src_ids,
            )
        )
        if mid:
            valid_ids.add(mid)
    return out


def _to_json_array(raw: str) -> object | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Tolerate prose/fences: take the outermost [ ... ] slice.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj


__all__ = ["MemoryOp", "extract_memories"]
