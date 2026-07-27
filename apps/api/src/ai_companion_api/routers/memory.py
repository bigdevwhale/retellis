"""``/v1/memory`` — event-chain timeline, recall, and cross-persona shares.

- ``GET /v1/memory?persona_id=…&limit=…`` → the user's events for a persona,
  oldest-first (the event-chain timeline the Memory panel renders).
- ``POST /v1/memory/recall`` → 2–4 intact chains ranked by query relevance +
  salience + recency, for "what does the companion remember about …" probes.
- ``GET/POST/DELETE /v1/memory/shares`` → cross-persona live memory links. A
  share is a *reference*, not a copy: the donor's memories stay owned by the
  donor; the receiver's read paths union them while the link exists. Donor-
  initiated — ``donor_persona_id`` shares INTO ``receiver_persona_id``.
- ``DELETE /v1/memory/convo`` → remove one conversation's raw message events
  server-side (the server half of "delete conversation"). Derived memories
  persist — un-learning is ``DELETE /v1/memory``.
- ``DELETE /v1/memory`` → un-learn everything for a persona: its events, its
  memories (all statuses), and its outgoing donor shares. Incoming shares from
  other personas are donor-owned and left intact.

Read-only except for the shares + the two reset deletes; never returns key
material. The store is in-memory by default and Postgres when
``COMPANION_USE_DB=1`` (see ``memory/store.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from ai_companion_contracts import (
    ConversationSummary,
    Event,
    EventChain,
    Memory,
    MemoryShare,
    Principal,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import get_current_principal, get_current_user_id, get_store
from ..memory.store import MemoryStore

router = APIRouter()

UserId = Annotated[str, Depends(get_current_user_id)]
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]
Store = Annotated[MemoryStore, Depends(get_store)]


def _require_family_match(principal: Principal, family_id: str | None) -> None:
    """Sprint 6 M1.3: a caller-supplied ``family_id`` must match the verified
    Principal's current family. The ``user_id`` store filter already prevents
    cross-user reads, but without this check a user who left a family can still
    query rows tagged with the old family_id, and the contract is inconsistent
    with ``/v1/llm/stream`` (which 404s on the same mismatch at L474–479).
    No match → 404 (not 403), per the project's cross-tenant convention."""
    if family_id is not None and principal.family_id != family_id:
        raise HTTPException(status_code=404, detail="Not found")


# ``before`` is a backward cursor with a ``None`` default. A ``Query(None, ...)``
# in an argument default trips ruff B008 (it allowlists ``fastapi.Query`` only
# for non-None defaults) — so hoist the ``Annotated`` alias to module level and
# give the param its ``None`` default with ``=`` on the signature, same idiom
# as ``FromQuery`` in journal.py and ``UserId`` / ``Store`` above.
BeforeQuery = Annotated[
    datetime | None,
    Query(
        description="Backward cursor (ISO 8601): return conversations with last_activity < before."
    ),
]


class MemoryRecallBody(BaseModel):
    persona_id: str
    query: str
    k: int = 3
    # I5: family scope for the recall probe — same solo/joint predicate as the
    # stream. Optional; omit for the legacy personal/non-family path.
    family_id: str | None = None
    visibility: str | None = None
    participant_user_id: str | None = None


def _normalize_family_scope(
    family_id: str | None, visibility: str | None
) -> tuple[str | None, str | None]:
    """I12: when ``family_id`` is set but ``visibility`` is not, default
    ``visibility`` to ``"private"``. Without this, the family-scope predicate
    is a no-op and ``GET /v1/memory?family_id=F`` returns the user's personal
    rows mixed with the family's — a scope leak. Defaulting to the solo
    predicate (shared + this member's own private) matches what the family
    therapist sees in a 1:1 and keeps personal rows out of family queries."""
    if family_id is not None and visibility is None:
        return family_id, "private"
    return family_id, visibility


class MemoryShareBody(BaseModel):
    donor_persona_id: str
    receiver_persona_id: str


@router.get("/memory", response_model=list[Event])
async def list_events(
    persona_id: str,
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
    limit: int = Query(50, ge=1, le=200),
    convo_id: str | None = Query(
        None, description="Filter to one conversation (K6: per-convo history load)."
    ),
    family_id: str | None = Query(
        None, description="Filter by family scope. None = personal (non-family) events only."
    ),
    visibility: str | None = Query(
        None,
        pattern="^(private|shared)$",
        description="Family visibility filter (paired with family_id).",
    ),
    participant_user_id: str | None = Query(
        None, description="For solo family recalls: the speaking member's user_id."
    ),
) -> list[Event]:
    _require_family_match(principal, family_id)
    family_id, visibility = _normalize_family_scope(family_id, visibility)
    return await store.list_events(
        user_id=user_id,
        persona_id=persona_id,
        limit=limit,
        convo_id=convo_id,
        family_id=family_id,
        visibility=visibility,
        participant_user_id=participant_user_id,
    )


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
    persona_id: str | None = Query(
        None, description="Scope to one persona. None = list across all the user's personas."
    ),
    before: BeforeQuery = None,
    limit: int = Query(50, ge=1, le=200),
    family_id: str | None = Query(
        None, description="Filter by family scope. None = personal (non-family) conversations only."
    ),
    visibility: str | None = Query(
        None,
        pattern="^(private|shared)$",
        description="Family visibility filter (paired with family_id).",
    ),
    participant_user_id: str | None = Query(
        None, description="For solo family scopes: the speaking member's user_id."
    ),
) -> list[ConversationSummary]:
    """K6: the conversation-list projection for the UI drawer. There is no
    ``conversations`` table — each row is derived from ``events`` grouped by
    ``convo_id`` (title = first user message, preview = last event, last_activity
    = MAX(created_at)). Ordered ``last_activity`` desc; ``before`` is a backward
    cursor for pagination. Survives refresh (unlike the old in-memory-only drawer)
    and is scoped by ``user_id`` — never crosses users. Read-only; no key
    material (events carry no keys)."""
    _require_family_match(principal, family_id)
    family_id, visibility = _normalize_family_scope(family_id, visibility)
    return await store.list_conversations(
        user_id=user_id,
        persona_id=persona_id,
        before=before,
        limit=limit,
        family_id=family_id,
        visibility=visibility,
        participant_user_id=participant_user_id,
    )


@router.delete("/memory/convo", status_code=204)
async def delete_convo_events(
    persona_id: str,
    convo_id: str,
    store: Store,
    user_id: UserId,
) -> None:
    """Remove one conversation's raw message events server-side — the server
    half of "delete conversation" (the client also drops the thread from its
    list). Derived memories are NOT touched: a memory's ``source_event_ids``
    can span several convos, so deleting a thread removes the messages, not the
    facts learned from them. Un-learning is ``DELETE /v1/memory``. Idempotent —
    a missing convo returns 204, not 404."""
    await store.delete_convo_events(user_id=user_id, persona_id=persona_id, convo_id=convo_id)


@router.delete("/memory", status_code=204)
async def wipe_persona_memory(
    persona_id: str,
    store: Store,
    user_id: UserId,
) -> None:
    """Un-learn everything for a persona: its events, its memories (every
    status), and its OUTGOING donor shares (it has nothing left to share into
    others). Incoming shares — where OTHER personas share INTO this one — are
    donor-owned and left intact; revoke those from the donor side. Idempotent —
    a persona with nothing stored returns 204, not 404. Never touches key
    material (memory/events carry no keys)."""
    await store.wipe_persona_memory(user_id=user_id, persona_id=persona_id)


@router.get("/memories", response_model=list[Memory])
async def list_memories(
    persona_id: str,
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
    family_id: str | None = Query(
        None, description="Filter by family scope. None = personal (non-family) memories only."
    ),
    visibility: str | None = Query(
        None,
        pattern="^(private|shared)$",
        description="Family visibility filter (paired with family_id).",
    ),
    participant_user_id: str | None = Query(
        None, description="For solo family recalls: the speaking member's user_id."
    ),
) -> list[Memory]:
    """The atomic, LLM-derived memories for a persona — the display unit of the
    /memory page. Only ``status='active'`` rows (superseded memories are
    hidden). Ordered by salience then recency. Read-only; no key material.

    Includes memories live-linked from donor personas (cross-persona shares);
    each row still carries its original ``persona_id`` so the UI can attribute
    "shared from {donor}".

    Family filter: pass ``family_id`` (and optionally ``visibility`` +
    ``participant_user_id``) to scope by the family session. The same
    solo/joint predicate the family therapist uses in the stream applies here:
    solo reads shared + own private; joint reads shared only."""
    _require_family_match(principal, family_id)
    family_id, visibility = _normalize_family_scope(family_id, visibility)
    return await store.list_memories(
        user_id=user_id,
        persona_id=persona_id,
        family_id=family_id,
        visibility=visibility,
        participant_user_id=participant_user_id,
    )


@router.post("/memory/recall", response_model=list[EventChain])
async def recall(
    body: MemoryRecallBody,
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
) -> list[EventChain]:
    _require_family_match(principal, body.family_id)
    family_id, visibility = _normalize_family_scope(body.family_id, body.visibility)
    return await store.recall_chains(
        user_id=user_id,
        persona_id=body.persona_id,
        query=body.query,
        k=body.k,
        family_id=family_id,
        visibility=visibility,
        participant_user_id=body.participant_user_id,
    )


@router.get("/memory/shares", response_model=list[MemoryShare])
async def list_shares(
    donor_persona_id: str,
    store: Store,
    user_id: UserId,
) -> list[MemoryShare]:
    """The receivers the given donor persona is currently sharing its memory
    with (donor-side management view on the Memory page)."""
    return await store.list_shares(user_id=user_id, donor_persona_id=donor_persona_id)


@router.post("/memory/shares", response_model=MemoryShare)
async def add_share(
    body: MemoryShareBody,
    store: Store,
    user_id: UserId,
) -> MemoryShare:
    """Create a donor→receiver live memory link. Idempotent — reposting the same
    triple returns the existing link. 400 on self-share (donor == receiver)."""
    try:
        return await store.add_share(
            user_id=user_id,
            donor_persona_id=body.donor_persona_id,
            receiver_persona_id=body.receiver_persona_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memory/shares", status_code=204)
async def remove_share(
    donor_persona_id: str,
    receiver_persona_id: str,
    store: Store,
    user_id: UserId,
) -> None:
    """Revoke a donor→receiver link. The donor's memories vanish from the
    receiver's read paths; nothing is deleted from the donor."""
    await store.remove_share(
        user_id=user_id,
        donor_persona_id=donor_persona_id,
        receiver_persona_id=receiver_persona_id,
    )
