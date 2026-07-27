"""Event-chain append — links each new event to the previous one in its convo.

``append_event`` scores salience + extracts emotion tags + embeds, sets the
``prev_event_id`` link to the convo's last event, and persists via the store.
Returns the constructed ``Event`` (with a private ``_convo_id`` attr the
in-memory store uses to filter recent windows; the Postgres store persists
``convo_id`` on the row directly).
"""

from __future__ import annotations

import asyncio
import uuid

from ai_companion_contracts import Event, EventRole

from .embeddings import embed
from .salience import SalienceScore, score_salience
from .store import MemoryStore

# I7: per-convo append lock. ``append_event`` reads the convo's last event id
# and then writes the new event linked to it; without serialization, two
# concurrent turns in the same convo both read the same ``prev_event_id`` and
# both append → the chain forks (two heads with the same parent). The lock
# makes the read-then-write atomic per ``(user_id, persona_id, convo_id)`` so
# the second turn links to the first's new event instead of the shared parent.
# Process-local (single asyncio loop); the in-memory and Postgres stores share
# it because both go through this function.
_convo_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def _convo_lock(user_id: str, persona_id: str, convo_id: str) -> asyncio.Lock:
    key = (user_id, persona_id, convo_id)
    lock = _convo_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _convo_locks[key] = lock
    return lock


async def append_event(
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    convo_id: str,
    role: EventRole,
    content: str,
    event_id: str | None = None,
    prev_event_id: str | None = None,
    salience_score: SalienceScore | None = None,
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
) -> Event:
    async with _convo_lock(user_id, persona_id, convo_id):
        if prev_event_id is None:
            prev_event_id = await store.last_event_id(
                user_id=user_id, persona_id=persona_id, convo_id=convo_id
            )

        # Precomputed values (from the LLM judge) win; else fall back to the
        # heuristic. The router passes judged salience for the user event when a
        # real provider served the turn; the assistant event and the offline path
        # use the heuristic.
        if salience_score is None:
            salience_score = score_salience(content)

        event = Event(
            id=event_id or uuid.uuid4().hex,
            user_id=user_id,
            persona_id=persona_id,
            prev_event_id=prev_event_id,
            role=role,
            content=content,
            salience=salience_score.salience,
            short_term_salience=salience_score.short_term_salience,
            emotional_intensity=salience_score.emotional_intensity,
            emotion_tags=salience_score.emotion_tags,
            # Family scope. The router validates the family_id/visibility/participant
            # tuple before calling; defaults here are the no-family (personal) path.
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        # Stash the embedding + convo_id where each store expects it. A
        # precomputed semantic vector (Phase 3a — computed in the post-turn
        # window while the BYOK key is legitimately alive) wins over the
        # zero-config hash vector; ``embedding_model`` records which space the
        # vector lives in (None = hash) so ANN recall never mixes spaces.
        event.__dict__["_embedding"] = embedding if embedding is not None else embed(content)  # noqa: SLF001
        event.__dict__["_embedding_model"] = embedding_model if embedding is not None else None  # noqa: SLF001
        event.__dict__["_convo_id"] = convo_id  # noqa: SLF001
        await store.add_event(event)
        return event


__all__ = ["append_event"]
