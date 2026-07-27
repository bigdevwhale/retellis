"""``/v1/journal`` — the user's diary, separate from the chat event chain.

- ``GET /v1/journal`` → entries for the user, newest first, with ILIKE search
  over title+body and facet filters (persona / tag / mood / date range) +
  limit/offset pagination.
- ``POST /v1/journal`` → create an entry (optionally seeded from a chat
  message via ``source_convo_id`` / ``source_event_id``).
- ``PATCH /v1/journal/{id}`` → partial update; absent keys keep the existing
  value, explicit null clears the nullable ``title`` / ``mood``.
- ``DELETE /v1/journal/{id}`` → remove one entry.

The journal surfaces ``mood`` and ``tags`` AS AUTHORED by the user — it never
generates affective claims ("disclose, don't perform"). ``salience`` is the
user's "matters to me" choice, not an LLM-judged score. No key material, no
LLM calls. The store is in-memory by default and Postgres when
``COMPANION_USE_DB=1`` (see ``memory/store.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from ai_companion_contracts import JournalEntry, JournalTagListResponse, Principal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import get_current_principal, get_current_user_id, get_store
from ..memory.store import MemoryStore

router = APIRouter()

UserId = Annotated[str, Depends(get_current_user_id)]
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]
Store = Annotated[MemoryStore, Depends(get_store)]


def _require_family_match(principal: Principal, family_id: str | None) -> None:
    """Sprint 6 M1.3: a caller-supplied ``family_id`` must match the verified
    Principal's current family — mirrors ``/v1/llm/stream`` and the memory
    router. Without this, a user could author a journal row tagged with a
    family they are not in (the row is still caller-owned, but the family-scope
    invariant on writes is not enforced). No match → 404 (not 403)."""
    if family_id is not None and principal.family_id != family_id:
        raise HTTPException(status_code=404, detail="Not found")


# ``from`` is a Python keyword, so the query param is named ``from`` on the wire
# but ``from_`` in code. The ``alias`` has to ride on a ``Query`` here, and a
# ``Query`` call in an argument default trips ruff B008 (it allowlists
# ``fastapi.Query`` only for non-None defaults) — so hoist the ``Annotated``
# alias to module level (same idiom as ``UserId`` / ``Store`` above) and give
# the param its ``None`` default with ``=`` on the signature, which FastAPI
# requires (it forbids a default inside ``Annotated[Query(default=...)]``).
FromQuery = Annotated[datetime | None, Query(alias="from")]


class JournalEntryCreate(BaseModel):
    persona_id: str
    body: str
    title: str | None = None
    mood: str | None = None
    tags: list[str] = Field(default_factory=list)
    salience: float = Field(0.0, ge=0, le=1)
    source_convo_id: str | None = None
    source_event_id: str | None = None
    # I13: family scope for the entry — same shape as Event/Memory. Optional;
    # omit (or leave family_id null) for a personal (non-family) entry, which
    # is the default the /journal page writes today. Setting family_id lets a
    # member author a family-scoped diary row (e.g. a family-session reflection)
    # that the family-scope wipe respects on leave/disband.
    family_id: str | None = None
    visibility: Literal["private", "shared"] = "private"
    participant_user_id: str | None = None


class JournalEntryUpdate(BaseModel):
    # All optional — only supplied keys mutate (PATCH semantics). Pydantic's
    # ``model_fields_set`` distinguishes "absent" from "explicit null"; we honor
    # that for the nullable ``title`` / ``mood`` so a client can clear them by
    # sending the key with JSON null. ``body`` / ``tags`` are required-on-row
    # but still patchable; absent keeps the existing value.
    title: str | None = None
    body: str | None = None
    mood: str | None = None
    tags: list[str] | None = None


@router.get("/journal/tags", response_model=JournalTagListResponse)
async def list_journal_tags(
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
    persona_id: str | None = None,
    mood: str | None = None,
    from_: FromQuery = None,
    to: datetime | None = None,
    family_id: str | None = Query(
        None,
        description="Filter by family scope. None = all the user's entries (personal + family).",
    ),
) -> JournalTagListResponse:
    """Distinct tag cloud for the /journal sidebar. Same scope as
    ``GET /v1/journal`` (persona/family/mood/date range) but WITHOUT the
    ``tag``/``q``/pagination inputs — the cloud is the source of truth for
    the tag-filter chips, so re-applying the tag filter would collapse the
    result. Sorted lexicographically for a stable UI; the server aggregates
    via the store's ``list_journal_tags`` (no N+1 on the client)."""
    _require_family_match(principal, family_id)
    tags = await store.list_journal_tags(
        user_id=user_id,
        persona_id=persona_id,
        mood=mood,
        from_dt=from_,
        to_dt=to,
        family_id=family_id,
    )
    return JournalTagListResponse(tags=tags)


@router.get("/journal", response_model=list[JournalEntry])
async def list_journal_entries(
    store: Store,
    user_id: UserId,
    principal: PrincipalDep,
    persona_id: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    mood: str | None = None,
    from_: FromQuery = None,
    to: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    family_id: str | None = Query(
        None,
        description="Filter by family scope. None = all the user's entries (personal + family).",
    ),
) -> list[JournalEntry]:
    """The user's journal entries, newest first. ILIKE ``q`` matches the body
    OR the optional title (case-insensitive, works for RU and EN). ``tag`` is
    JSONB containment (entries whose ``tags`` array includes it). ``from`` /
    ``to`` bound ``created_at``. Scoped to the caller's ``user_id``. ``family_id``
    (M1.3) optionally filters to one family scope — must match the Principal's
    current family or the endpoint 404s."""
    _require_family_match(principal, family_id)
    return await store.list_journal_entries(
        user_id=user_id,
        persona_id=persona_id,
        q=q,
        tag=tag,
        mood=mood,
        from_dt=from_,
        to_dt=to,
        limit=limit,
        offset=offset,
        family_id=family_id,
    )


@router.post("/journal", response_model=JournalEntry)
async def create_journal_entry(
    body: JournalEntryCreate, user_id: UserId, principal: PrincipalDep, store: Store
) -> JournalEntry:
    _require_family_match(principal, body.family_id)
    return await store.add_journal_entry(
        user_id=user_id,
        persona_id=body.persona_id,
        title=body.title,
        body=body.body,
        mood=body.mood,
        tags=body.tags,
        salience=body.salience,
        source_convo_id=body.source_convo_id,
        source_event_id=body.source_event_id,
        family_id=body.family_id,
        visibility=body.visibility,
        participant_user_id=body.participant_user_id,
    )


@router.patch("/journal/{eid}", response_model=JournalEntry)
async def update_journal_entry(
    eid: str, body: JournalEntryUpdate, user_id: UserId, store: Store
) -> JournalEntry:
    existing = await store.get_journal_entry(user_id=user_id, entry_id=eid)
    if existing is None:
        raise HTTPException(status_code=404, detail="journal entry not found")
    fields_set = body.model_fields_set
    title = body.title if "title" in fields_set else existing.title
    body_ = body.body if "body" in fields_set else existing.body
    mood = body.mood if "mood" in fields_set else existing.mood
    tags = body.tags if "tags" in fields_set else existing.tags
    if body_ is None or body_ == "":
        # ``body`` is required on the row — refuse to clear it via PATCH.
        raise HTTPException(status_code=422, detail="body cannot be empty")
    updated = await store.update_journal_entry(
        user_id=user_id,
        entry_id=eid,
        title=title,
        body=body_,
        mood=mood,
        tags=list(tags or []),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="journal entry not found")
    return updated


@router.delete("/journal/{eid}", status_code=204)
async def delete_journal_entry(eid: str, user_id: UserId, store: Store) -> None:
    deleted = await store.delete_journal_entry(user_id=user_id, entry_id=eid)
    if not deleted:
        raise HTTPException(status_code=404, detail="journal entry not found")
