"""Session bridge (P0 #4) — one factual line linking a NEW conversation to the
previous one.

The first turn of a fresh convo is where "she remembers me" is won or lost,
and it is exactly where the context is weakest: the recent window is empty and
the retrieval query is usually a greeting. ``build_session_bridge`` fetches
the most recent *other* conversation of the same persona/scope and renders its
tail with a relative age ("Your previous conversation (5 days ago) …"). Pure
DB reads, no LLM call; best-effort — returns ``None`` on any failure or when
there is no prior conversation.

Lives in the memory package (not the router) so the eval gate can probe it
litellm-/fastapi-free.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .recall import relative_time
from .store import MemoryStore

# Each rendered line is capped — the bridge is a one-line orientation aid,
# not a transcript replay.
BRIDGE_MAX_EVENTS = 6
BRIDGE_MAX_CHARS = 200


async def build_session_bridge(
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    convo_id: str,
    family_id: str | None = None,
    visibility: str | None = None,
    participant_user_id: str | None = None,
    family_members: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, str] | None:
    try:
        convos = await store.list_conversations(
            user_id=user_id,
            persona_id=persona_id,
            limit=5,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        prev = next((c for c in convos if c.convo_id != convo_id), None)
        if prev is None:
            return None
        tail = await store.recent_window(
            user_id=user_id,
            persona_id=persona_id,
            convo_id=prev.convo_id,
            limit=BRIDGE_MAX_EVENTS,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        fm = family_members or {}
        lines: list[str] = []
        for e in tail:
            role = e.role.value if hasattr(e.role, "value") else str(e.role)
            if role not in ("user", "assistant"):
                continue
            if role == "user" and e.participant_user_id and e.participant_user_id in fm:
                label = fm[e.participant_user_id]
            else:
                label = "they said" if role == "user" else "you said"
            lines.append(f"{label}: {e.content[:BRIDGE_MAX_CHARS]}")
        if not lines:
            return None
        when = relative_time(prev.last_activity, now or datetime.now(UTC))
        return {
            "role": "system",
            "content": (
                f"Your previous conversation with them ({when}) ended with: "
                f"{' | '.join(lines)}. If it is natural, you may pick that thread back up; "
                "do not force it."
            ),
        }
    except Exception:  # bridge is best-effort — never break a turn
        return None


__all__ = ["BRIDGE_MAX_CHARS", "BRIDGE_MAX_EVENTS", "build_session_bridge"]
