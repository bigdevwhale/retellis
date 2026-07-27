"""Event + usage persistence.

Two implementations share one Protocol:

- ``InMemoryStore`` — process-local, used by tests and as the zero-config default
  (``COMPANION_USE_DB`` unset, or set but the DB is unreachable). Memory persists
  for the lifetime of the API process; cross-restart persistence needs Postgres.
- ``PostgresStore`` — async SQLAlchemy + pgvector, used in ``docker compose``
  when ``COMPANION_USE_DB=1``. Embeddings land in the ``events.embedding``
  ``vector(384)`` column; recall uses exact cosine (``<=>``) until >50k events.

The factory ``make_store(settings)`` picks the right one and falls back to
in-memory on any connection failure so the API never fails to boot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ai_companion_contracts import (
    ConversationSummary,
    Event,
    EventChain,
    EventRole,
    JournalEntry,
    Memory,
    MemoryShare,
    MemoryStatus,
    Provider,
    ProviderKind,
    Usage,
)

from ..clock import utcnow
from ..config import Settings
from .embeddings import embed
from .recall import rank_and_chain


@dataclass
class UsageRecord:
    """A usage row plus the timestamp it was recorded.

    The ``Usage`` contract has no timestamp (it is also surfaced as an SSE
    event mid-stream); the store attaches one on persist so the budget rollup
    can filter to the current month.
    """

    usage: Usage
    created_at: datetime


@runtime_checkable
class MemoryStore(Protocol):
    """Async event + usage store. All methods are awaitable."""

    async def add_event(self, event: Event) -> None: ...
    async def list_events(
        self,
        *,
        user_id: str,
        persona_id: str,
        limit: int = 50,
        convo_id: str | None = None,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]: ...
    async def recent_window(
        self,
        *,
        user_id: str,
        persona_id: str,
        convo_id: str,
        limit: int = 6,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]: ...
    async def recall_candidates(
        self,
        *,
        user_id: str,
        persona_id: str,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]: ...
    async def last_event_id(
        self, *, user_id: str, persona_id: str, convo_id: str
    ) -> str | None: ...
    # Phase 2a: reinforcement on recall — events that were actually surfaced
    # into a turn's context get a small salience bump (capped at 1.0), which
    # counteracts time decay for what keeps coming up. Scoped by ``user_id``
    # (an id from another user is a no-op). Best-effort at the call site.
    async def reinforce_events(
        self, *, user_id: str, event_ids: list[str], boost: float = 0.02
    ) -> None: ...
    async def add_usage(self, usage: Usage) -> None: ...
    async def list_usage(self, *, user_id: str) -> list[UsageRecord]: ...
    # K7 / budget-rollup fix: a family turn's spend must aggregate across ALL
    # members of the family, not just the requesting member. ``list_usage`` is
    # per-user (one member's rows); this returns every usage row tagged with
    # ``family_id == family_id`` regardless of which member incurred it, so the
    # family budget gate and the /v1/routing family view count the whole family.
    async def list_usage_by_family(self, *, family_id: str) -> list[UsageRecord]: ...
    # K6: conversation-list projection (derived from events grouped by
    # convo_id). ``persona_id`` is optional — None lists across all the
    # user's personas. ``before`` is a backward cursor (last_activity < before);
    # results are ordered ``last_activity`` desc and capped at ``limit``.
    async def list_conversations(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[ConversationSummary]: ...
    # ``embedder`` is an optional per-request ``SemanticEmbedder`` override
    # (BYOK: built around the turn's ECDH-sealed key + the user's
    # ``embeddings_model``). Precedence: override → store's env-configured
    # embedder → hash. Any embedding failure falls back to hash silently.
    async def recall_chains(
        self,
        *,
        user_id: str,
        persona_id: str,
        query: str,
        k: int = 3,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        embedder: object | None = None,
    ) -> list[EventChain]: ...
    # --- atomic memories (display layer over the event chain) ---
    async def add_memory(self, memory: Memory) -> None: ...
    # P2: ``include_superseded`` widens the result to superseded rows too —
    # used by the router's fact-pool so era-compressed episode DETAIL stays
    # reachable by relevance (the active layer shows only the era summary).
    # Default False keeps every other caller (routers, extractor, wipes) on
    # the active-only contract.
    async def list_memories(
        self,
        *,
        user_id: str,
        persona_id: str,
        include_donors: bool = True,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Memory]: ...
    # I10 / Sprint 6 M1.1: ``user_id`` (+ ``persona_id`` / ``family_id``) are
    # required so the store re-scopes the row to the caller — defense-in-depth
    # on top of ``_apply_memory_ops``'s ``existing_ids`` gate. Without this, a
    # caller with another user's ``memory_id`` could mutate/supersede it. The
    # ``family_id`` filter is applied only when not None (matches the
    # ``_apply_family_scope`` ``family_id is None`` ⇒ no-scope-filter convention).
    async def update_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        content: str,
        tags: list[str],
        salience: float,
        source_event_ids: list[str],
        family_id: str | None = None,
    ) -> None: ...
    async def supersede_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        superseded_by: str | None = None,
        family_id: str | None = None,
    ) -> None: ...
    # --- cross-persona live memory shares (donor → receiver link, a reference) ---
    async def add_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> MemoryShare: ...
    async def remove_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> None: ...
    async def list_shares(self, *, user_id: str, donor_persona_id: str) -> list[MemoryShare]: ...
    async def list_donors(self, *, user_id: str, receiver_persona_id: str) -> list[str]: ...
    # --- reset: per-convo event delete + full persona memory wipe ---
    # ``delete_convo_events`` removes one thread's raw message events (the
    # server-side half of "delete conversation"); derived memories persist —
    # un-learning is ``wipe_persona_memory``. ``wipe_persona_memory`` deletes the
    # persona's events + memories (all statuses) + its OUTGOING donor shares
    # (it has nothing left to share out); incoming shares from other personas
    # are donor-owned and left intact.
    async def delete_convo_events(self, *, user_id: str, persona_id: str, convo_id: str) -> int: ...
    async def wipe_persona_memory(self, *, user_id: str, persona_id: str) -> None: ...
    # I14: family-scope wipes — called unguarded by ``routers/family.py`` on
    # leave-member (``wipe_member_in_family`` — drops the member's PRIVATE rows
    # in this family; the shared layer survives) and on disband
    # (``wipe_family_scope`` — drops EVERY row in the family scope across
    # events + memories + journal + the per-family usage rollup). Declared on
    # the Protocol so a store impl that omits them fails loudly at type-check
    # time instead of producing an ``AttributeError`` at the family endpoint.
    # ``runtime_checkable`` doesn't enforce signatures, but both impls match.
    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None: ...
    async def wipe_family_scope(self, *, family_id: str) -> None: ...
    # --- providers (BYOK metadata + server-side envelope-encrypted key) ---
    # ``api_key_ciphertext`` is the envelope-encrypted BYOK key (migration
    # 0023). It is passed as a SEPARATE kwarg through the store layer only —
    # it must NEVER appear on the contract ``Provider`` model (never returned
    # to the client). ``get_provider_api_key_ciphertext`` is the per-turn
    # resolution read: keyed by ``(user_id, key_handle)`` so the LLM stream
    # can resolve the active key without a per-turn client blob.
    async def add_provider(
        self, provider: Provider, *, api_key_ciphertext: str | None = None
    ) -> Provider: ...
    async def list_providers(self, *, user_id: str) -> list[Provider]: ...
    async def get_provider(self, *, user_id: str, provider_id: str) -> Provider | None: ...
    async def get_provider_api_key_ciphertext(
        self, *, user_id: str, key_handle: str
    ) -> str | None: ...
    async def delete_provider(self, *, user_id: str, provider_id: str) -> bool: ...
    # Partial update — only ``label`` / ``base_url`` / ``model`` /
    # ``embeddings_model`` may change here. ``key_handle`` and the
    # zero-knowledge ``enc_blob`` are immutable through this path; the API key
    # itself stays in the client vault. Returns ``None`` if the row doesn't
    # exist for this user.
    async def update_provider(
        self,
        *,
        user_id: str,
        provider_id: str,
        label: str,
        base_url: str | None,
        model: str | None,
        embeddings_model: str | None = None,
    ) -> Provider | None: ...
    # --- journal (user-authored diary entries; the /journal page) ---
    # Separate from the chat event chain. Entries are written by the user (or
    # seeded from a chat message via "Save to journal"). ``list_journal_entries``
    # supports ILIKE search over title+body and facet filters (persona / tag /
    # mood / date range) with limit/offset pagination, ordered created_at desc.
    # ``update_journal_entry`` takes already-resolved values (the router handles
    # absent-vs-null PATCH semantics) and bumps updated_at.
    async def add_journal_entry(
        self,
        *,
        user_id: str,
        persona_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
        salience: float,
        source_convo_id: str | None,
        source_event_id: str | None,
        family_id: str | None = None,
        visibility: str = "private",
        participant_user_id: str | None = None,
    ) -> JournalEntry: ...
    async def list_journal_entries(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        q: str | None = None,
        tag: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        family_id: str | None = None,
    ) -> list[JournalEntry]: ...
    async def get_journal_entry(self, *, user_id: str, entry_id: str) -> JournalEntry | None: ...
    # Distinct tag cloud for the /journal sidebar. Aggregates over the same
    # scope as ``list_journal_entries`` minus the ``tag``/``q``/pagination
    # inputs — picking a tag filter must not collapse the cloud to ["<that>"].
    # Returns sorted unique tags. Empty when the user has no entries in scope.
    async def list_journal_tags(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        family_id: str | None = None,
    ) -> list[str]: ...
    async def update_journal_entry(
        self,
        *,
        user_id: str,
        entry_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
    ) -> JournalEntry | None: ...
    async def delete_journal_entry(self, *, user_id: str, entry_id: str) -> bool: ...


def _new_id() -> str:
    return uuid.uuid4().hex


def _apply_family_scope(
    row: Event | Memory | JournalEntry,
    *,
    family_id: str | None,
    visibility: str | None,
    participant_user_id: str | None,
) -> bool:
    """Family-scope recall predicate. Returns True iff the row is visible.

    Recall predicates (see PLAN §Family):
    - ``family_id is None`` — no scope filter (back-compat for non-family rows
      and for the eval gate, which never goes through the family path).
    - ``visibility == "shared"`` (joint session) — only ``family_id == F AND
      visibility == "shared"`` rows are visible. Private rows NEVER leak into
      the joint session, regardless of participant.
    - ``visibility == "private"`` (member M's solo 1:1) — visible if
      ``family_id == F AND (visibility == "shared" OR (visibility == "private"
      AND participant_user_id == M))``. M can see shared + their own private
      disclosures, never another member's private.

    The predicate is applied ONLY to rows the user owns (i.e. rows with
    ``user_id`` equal to the principal); donor-share rows are unioned
    unscoped by the caller (``recall_candidates``/``list_memories``).

    Post-MVP (PLAN §16 #1, #6): donor rows are deliberately NOT unioned in
    family-scope recall — a donor MemoryShare is a personal-scope link and
    must not surface in family sessions. The ``recall_candidates`` /
    ``list_memories`` impls gate the donor UNION on
    ``family_id is None or visibility is None``, so this helper stays
    unchanged and the invariant lives in the caller.
    """
    if family_id is None or visibility is None:
        return True
    if getattr(row, "family_id", None) != family_id:
        return False
    if visibility == "shared":
        return getattr(row, "visibility", "private") == "shared"
    # visibility == "private" — solo-M predicate
    if getattr(row, "visibility", "private") == "shared":
        return True
    return getattr(row, "participant_user_id", None) == participant_user_id


def _owns_or_shared(
    row: Event | Memory | JournalEntry,
    *,
    user_id: str,
    family_id: str | None,
    visibility: str | None,
) -> bool:
    """Ownership predicate for family-scope reads — the joint-session fix.

    A row is admissible when the requester owns it, OR — in a joint
    (``visibility == "shared"``) family session — when it is ANY member's
    shared row in the same family. The joint session is one shared thread
    per family: every member sees every other member's shared messages, so
    the strict ``user_id == requester`` ownership filter must be relaxed for
    the shared scope (it used to drop other members' shared rows before
    ``_apply_family_scope`` ever ran). Private/solo scope keeps the strict
    ownership filter: a member never sees another member's 1:1 with the
    therapist. ``family_id`` is still pinned to the requester's family by
    ``_require_family_match`` / the stream's ``_validate_family_scope``, so
    relaxing ``user_id`` for shared cannot leak another family's events."""
    if getattr(row, "user_id", None) == user_id:
        return True
    if family_id is not None and visibility == "shared":
        return (
            getattr(row, "family_id", None) == family_id
            and getattr(row, "visibility", "private") == "shared"
        )
    return False


# --- in-memory ---------------------------------------------------------------


class InMemoryStore:
    """Process-local store. Thread-unsafe by design (single asyncio loop)."""

    def __init__(self, semantic_embedder=None) -> None:  # type: ignore[no-untyped-def]
        # Optional ``SemanticEmbedder`` (embeddings_semantic.py). When set,
        # ``recall_chains`` ranks with batched semantic vectors and falls back
        # to the hash embedder on any embedding failure.
        self._semantic = semantic_embedder
        self._events: list[Event] = []
        self._usage: list[UsageRecord] = []
        self._memories: list[Memory] = []
        self._shares: list[MemoryShare] = []
        self._providers: list[Provider] = []
        # Server-side envelope-encrypted BYOK key ciphertext (migration 0023),
        # keyed by provider id. Kept separate from the contract ``Provider``
        # objects so the ciphertext is never returned by ``list_providers`` /
        # ``get_provider`` (which return contract ``Provider`` instances).
        self._provider_api_key_ciphertext: dict[str, str] = {}
        self._journal: list[JournalEntry] = []
        # K6: per-event created_at for the conversation-list projection. The
        # ``Event`` contract has no timestamp (it is also surfaced as an SSE
        # event mid-stream); the in-memory store attaches one on persist so
        # ``list_conversations`` can derive created_at/last_activity. The
        # PostgresStore reads ``created_at`` from the row instead. Timestamps
        # come from ``clock.utcnow()`` (strictly monotonic) so two writes in
        # the same ~1ms Windows clock tick still order deterministically.
        self._event_ts: dict[str, datetime] = {}

    async def add_event(self, event: Event) -> None:
        self._events.append(event)
        ts = self._event_ts.setdefault(event.id, utcnow())
        # Phase 2a: stamp the contract field too so recall's time-based decay
        # sees the same timestamp the projection layer uses.
        if event.created_at is None:
            event.created_at = ts

    async def list_events(
        self,
        *,
        user_id: str,
        persona_id: str,
        limit: int = 50,
        convo_id: str | None = None,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        rows = [
            e
            for e in self._events
            if e.persona_id == persona_id
            and _owns_or_shared(e, user_id=user_id, family_id=family_id, visibility=visibility)
        ]
        if convo_id is not None:
            rows = [e for e in rows if _convo(e) == convo_id]
        rows = [
            e
            for e in rows
            if _apply_family_scope(
                e,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        ]
        return rows[-limit:]

    async def recent_window(
        self,
        *,
        user_id: str,
        persona_id: str,
        convo_id: str,
        limit: int = 6,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        rows = [
            e
            for e in self._events
            if e.persona_id == persona_id
            and _convo(e) == convo_id
            and _owns_or_shared(e, user_id=user_id, family_id=family_id, visibility=visibility)
            and _apply_family_scope(
                e,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        ]
        return rows[-limit:]

    async def recall_candidates(
        self,
        *,
        user_id: str,
        persona_id: str,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        donors = await self.list_donors(user_id=user_id, receiver_persona_id=persona_id)
        personas = {persona_id, *donors}
        own = [
            e
            for e in self._events
            if e.persona_id in personas
            and _owns_or_shared(e, user_id=user_id, family_id=family_id, visibility=visibility)
            and _apply_family_scope(
                e,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        ]
        # Donor rows are unioned unscoped (existing per-user MemoryShare
        # semantics, unchanged by the family dimension).
        if donors and (family_id is None or visibility is None):
            donor_rows = [
                e for e in self._events if e.user_id == user_id and e.persona_id in donors
            ]
            return own + [e for e in donor_rows if e not in own]
        return own

    async def last_event_id(self, *, user_id: str, persona_id: str, convo_id: str) -> str | None:
        rows = [
            e
            for e in self._events
            if e.user_id == user_id and e.persona_id == persona_id and _convo(e) == convo_id
        ]
        return rows[-1].id if rows else None

    async def reinforce_events(
        self, *, user_id: str, event_ids: list[str], boost: float = 0.02
    ) -> None:
        wanted = set(event_ids)
        for e in self._events:
            if e.id in wanted and e.user_id == user_id:
                e.salience = min(1.0, float(e.salience) + boost)

    async def list_conversations(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[ConversationSummary]:
        # ``self._events`` is append-ordered, which IS chronological for the
        # in-memory store — so the first event seen per convo is the earliest
        # and the last is the most recent. Group preserving that order.
        convo_map: dict[str, list[tuple[Event, datetime]]] = {}
        for e in self._events:
            if not _owns_or_shared(
                e, user_id=user_id, family_id=family_id, visibility=visibility
            ):
                continue
            if persona_id is not None and e.persona_id != persona_id:
                continue
            if not _apply_family_scope(
                e,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            ):
                continue
            convo_map.setdefault(_convo(e), []).append(
                (e, self._event_ts.get(e.id, datetime.now(UTC)))
            )
        # Drop the empty-convo ("") bucket — events without a convo_id don't
        # form a listable conversation.
        convo_map.pop("", None)
        return _convo_summaries(convo_map, before=before, limit=limit)

    async def add_usage(self, usage: Usage) -> None:
        self._usage.append(UsageRecord(usage=usage, created_at=utcnow()))

    async def list_usage(self, *, user_id: str) -> list[UsageRecord]:
        return [r for r in self._usage if r.usage.user_id == user_id]

    async def list_usage_by_family(self, *, family_id: str) -> list[UsageRecord]:
        return [r for r in self._usage if r.usage.family_id == family_id]

    async def recall_chains(
        self,
        *,
        user_id: str,
        persona_id: str,
        query: str,
        k: int = 3,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        embedder: object | None = None,
    ) -> list[EventChain]:
        cands = await self.recall_candidates(
            user_id=user_id,
            persona_id=persona_id,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        return await _rank_chains(cands, query, k, semantic=embedder or self._semantic)

    async def add_memory(self, memory: Memory) -> None:
        self._memories.append(memory)

    async def list_memories(
        self,
        *,
        user_id: str,
        persona_id: str,
        include_donors: bool = True,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Memory]:
        if include_donors:
            donors = await self.list_donors(user_id=user_id, receiver_persona_id=persona_id)
            personas = {persona_id, *donors}
        else:
            personas = {persona_id}
        statuses = {MemoryStatus.active}
        if include_superseded:
            statuses.add(MemoryStatus.superseded)
        rows = [
            m
            for m in self._memories
            if m.user_id == user_id
            and m.persona_id in personas
            and m.status in statuses
            and _apply_family_scope(
                m,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        ]
        # Salience first (what matters most on top), then most-recently-updated.
        rows.sort(key=lambda m: (m.salience, m.updated_at), reverse=True)
        return rows

    async def update_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        content: str,
        tags: list[str],
        salience: float,
        source_event_ids: list[str],
        family_id: str | None = None,
    ) -> None:
        for i, m in enumerate(self._memories):
            if m.id != memory_id:
                continue
            # I10 / M1.1: re-scope to the caller. A memory id from another user
            # (or another persona / family scope) is a no-op, not a mutation.
            if m.user_id != user_id or m.persona_id != persona_id:
                return
            if family_id is not None and m.family_id != family_id:
                return
            merged = list(dict.fromkeys([*m.source_event_ids, *source_event_ids]))
            self._memories[i] = m.model_copy(
                update={
                    "content": content,
                    "tags": tags,
                    "salience": salience,
                    "source_event_ids": merged,
                    "updated_at": utcnow(),
                }
            )
            return

    async def supersede_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        superseded_by: str | None = None,
        family_id: str | None = None,
    ) -> None:
        for i, m in enumerate(self._memories):
            if m.id != memory_id or m.status != MemoryStatus.active:
                continue
            if m.user_id != user_id or m.persona_id != persona_id:
                return
            if family_id is not None and m.family_id != family_id:
                return
            self._memories[i] = m.model_copy(
                update={
                    "status": MemoryStatus.superseded,
                    "superseded_by": superseded_by,
                    "updated_at": utcnow(),
                }
            )
            return

    # --- cross-persona live memory shares ---

    async def add_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> MemoryShare:
        if donor_persona_id == receiver_persona_id:
            raise ValueError("cannot share a persona's memory with itself")
        for sh in self._shares:
            if (
                sh.user_id == user_id
                and sh.donor_persona_id == donor_persona_id
                and sh.receiver_persona_id == receiver_persona_id
            ):
                return sh  # idempotent — the link already exists
        share = MemoryShare(
            id=_new_id(),
            user_id=user_id,
            donor_persona_id=donor_persona_id,
            receiver_persona_id=receiver_persona_id,
            created_at=utcnow(),
        )
        self._shares.append(share)
        return share

    async def remove_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> None:
        self._shares = [
            sh
            for sh in self._shares
            if not (
                sh.user_id == user_id
                and sh.donor_persona_id == donor_persona_id
                and sh.receiver_persona_id == receiver_persona_id
            )
        ]

    async def list_shares(self, *, user_id: str, donor_persona_id: str) -> list[MemoryShare]:
        return [
            sh
            for sh in self._shares
            if sh.user_id == user_id and sh.donor_persona_id == donor_persona_id
        ]

    async def list_donors(self, *, user_id: str, receiver_persona_id: str) -> list[str]:
        return [
            sh.donor_persona_id
            for sh in self._shares
            if sh.user_id == user_id and sh.receiver_persona_id == receiver_persona_id
        ]

    # --- reset: per-convo event delete + full persona memory wipe ---

    async def delete_convo_events(self, *, user_id: str, persona_id: str, convo_id: str) -> int:
        before = len(self._events)
        self._events = [
            e
            for e in self._events
            if not (e.user_id == user_id and e.persona_id == persona_id and _convo(e) == convo_id)
        ]
        return before - len(self._events)

    async def wipe_persona_memory(self, *, user_id: str, persona_id: str) -> None:
        # Drop this persona's own events + memories (every status) + its
        # outgoing donor shares. Incoming shares (receiver == persona) are
        # donor-owned by OTHER personas — leave them.
        self._events = [
            e for e in self._events if not (e.user_id == user_id and e.persona_id == persona_id)
        ]
        self._memories = [
            m for m in self._memories if not (m.user_id == user_id and m.persona_id == persona_id)
        ]
        self._shares = [
            sh
            for sh in self._shares
            if not (sh.user_id == user_id and sh.donor_persona_id == persona_id)
        ]

    # --- family-scope wipes ---
    # Used by routers/family.py on leave / remove-member / disband. The
    # in-memory impl just filters lists; the Postgres impl will issue DELETEs
    # in a single transaction. The family-store wipe is separate (members +
    # invites + family row).
    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None:
        # Drop the member's PRIVATE rows in this family only. The shared layer
        # belongs to the family and is unaffected. Assistant-role private rows
        # (participant_user_id IS NULL) are NOT a member's own private — skip.
        self._events = [
            e
            for e in self._events
            if not (
                e.user_id == user_id
                and e.family_id == family_id
                and e.visibility == "private"
                and e.participant_user_id == user_id
            )
        ]
        self._memories = [
            m
            for m in self._memories
            if not (
                m.user_id == user_id
                and m.family_id == family_id
                and m.visibility == "private"
                and m.participant_user_id == user_id
            )
        ]
        # Journal entries are user-authored diary rows; private ones in the
        # family scope belong to the member and should go.
        self._journal = [
            j
            for j in self._journal
            if not (
                j.user_id == user_id
                and j.family_id == family_id
                and j.visibility == "private"
                and j.participant_user_id == user_id
            )
        ]

    async def wipe_family_scope(self, *, family_id: str) -> None:
        # Drop EVERY row in the family scope (events + memories + journal) plus
        # the per-family usage rollup. Shared + private alike — disband is a
        # nuclear reset of family data.
        self._events = [e for e in self._events if e.family_id != family_id]
        self._memories = [m for m in self._memories if m.family_id != family_id]
        self._journal = [j for j in self._journal if j.family_id != family_id]
        self._usage = [r for r in self._usage if r.usage.family_id != family_id]

    # --- providers (BYOK metadata + zero-knowledge enc_blob at-rest backup) ---

    async def add_provider(
        self, provider: Provider, *, api_key_ciphertext: str | None = None
    ) -> Provider:
        self._providers.append(provider)
        if api_key_ciphertext is not None:
            self._provider_api_key_ciphertext[provider.id] = api_key_ciphertext
        return provider

    async def list_providers(self, *, user_id: str) -> list[Provider]:
        return [p for p in self._providers if p.user_id == user_id]

    async def get_provider(self, *, user_id: str, provider_id: str) -> Provider | None:
        return next(
            (p for p in self._providers if p.user_id == user_id and p.id == provider_id), None
        )

    async def get_provider_api_key_ciphertext(
        self, *, user_id: str, key_handle: str
    ) -> str | None:
        for p in self._providers:
            if p.user_id == user_id and p.key_handle == key_handle:
                return self._provider_api_key_ciphertext.get(p.id)
        return None

    async def delete_provider(self, *, user_id: str, provider_id: str) -> bool:
        before = len(self._providers)
        self._providers = [
            p for p in self._providers if not (p.user_id == user_id and p.id == provider_id)
        ]
        self._provider_api_key_ciphertext.pop(provider_id, None)
        return len(self._providers) != before

    async def update_provider(
        self,
        *,
        user_id: str,
        provider_id: str,
        label: str,
        base_url: str | None,
        model: str | None,
        embeddings_model: str | None = None,
    ) -> Provider | None:
        for i, p in enumerate(self._providers):
            if p.user_id == user_id and p.id == provider_id:
                updated = p.model_copy(
                    update={
                        "label": label,
                        "base_url": base_url,
                        "model": model,
                        "embeddings_model": embeddings_model,
                    }
                )
                self._providers[i] = updated
                return updated
        return None

    # --- journal (user-authored diary entries) ---

    async def add_journal_entry(
        self,
        *,
        user_id: str,
        persona_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
        salience: float,
        source_convo_id: str | None,
        source_event_id: str | None,
        family_id: str | None = None,
        visibility: str = "private",
        participant_user_id: str | None = None,
    ) -> JournalEntry:
        now = utcnow()
        entry = JournalEntry(
            id=_new_id(),
            user_id=user_id,
            persona_id=persona_id,
            title=title,
            body=body,
            mood=mood,
            tags=list(tags),
            salience=salience,
            source_convo_id=source_convo_id,
            source_event_id=source_event_id,
            created_at=now,
            updated_at=now,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        self._journal.append(entry)
        return entry

    async def list_journal_entries(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        q: str | None = None,
        tag: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        family_id: str | None = None,
    ) -> list[JournalEntry]:
        needle = q.strip().lower() if q else ""
        rows = [
            e
            for e in self._journal
            if e.user_id == user_id
            and (persona_id is None or e.persona_id == persona_id)
            and (family_id is None or e.family_id == family_id)
            and (mood is None or e.mood == mood)
            and (tag is None or tag in e.tags)
            and (from_dt is None or e.created_at >= from_dt)
            and (to_dt is None or e.created_at <= to_dt)
            and (
                not needle or needle in (e.body or "").lower() or needle in (e.title or "").lower()
            )
        ]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[offset : offset + limit]

    async def list_journal_tags(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        family_id: str | None = None,
    ) -> list[str]:
        # Mirror the filter set of ``list_journal_entries`` minus ``tag``/``q``/
        # pagination. ``tag`` is omitted on purpose — the cloud is the source
        # of truth for filter chips, so re-applying it would collapse the result
        # to the selected tag(s). Empty ``user_id`` is a defensive guard, not
        # the auth boundary (the router resolves ``user_id`` from the session).
        if not user_id:
            return []
        seen: set[str] = set()
        for e in self._journal:
            if e.user_id != user_id:
                continue
            if persona_id is not None and e.persona_id != persona_id:
                continue
            if family_id is not None and e.family_id != family_id:
                continue
            if mood is not None and e.mood != mood:
                continue
            if from_dt is not None and e.created_at < from_dt:
                continue
            if to_dt is not None and e.created_at > to_dt:
                continue
            for tag in e.tags or []:
                if tag:
                    seen.add(tag)
        return sorted(seen)

    async def get_journal_entry(self, *, user_id: str, entry_id: str) -> JournalEntry | None:
        return next((e for e in self._journal if e.user_id == user_id and e.id == entry_id), None)

    async def update_journal_entry(
        self,
        *,
        user_id: str,
        entry_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
    ) -> JournalEntry | None:
        for i, e in enumerate(self._journal):
            if e.user_id == user_id and e.id == entry_id:
                updated = e.model_copy(
                    update={
                        "title": title,
                        "body": body,
                        "mood": mood,
                        "tags": list(tags),
                        "updated_at": utcnow(),
                    }
                )
                self._journal[i] = updated
                return updated
        return None

    async def delete_journal_entry(self, *, user_id: str, entry_id: str) -> bool:
        before = len(self._journal)
        self._journal = [
            e for e in self._journal if not (e.user_id == user_id and e.id == entry_id)
        ]
        return len(self._journal) != before


# --- Postgres (lazy) ---------------------------------------------------------


class PostgresStore:
    """SQLAlchemy async + pgvector. Imported lazily so the in-memory path
    doesn't require a running Postgres or the asyncpg/pgvector packages at
    import time."""

    def __init__(self, settings: Settings, semantic_embedder=None) -> None:  # type: ignore[no-untyped-def]
        self._settings = settings
        # Optional ``SemanticEmbedder`` — see InMemoryStore.
        self._semantic = semantic_embedder

    async def _session(self):
        from ..db.session import get_sessionmaker  # lazy

        sm = get_sessionmaker(self._settings)
        return sm()

    async def add_event(self, event: Event) -> None:
        from ..db import models as m  # lazy

        convo_id = getattr(event, "_convo_id", "") or ""
        embedding = getattr(event, "_embedding", None) or embed(event.content)
        async with await self._session() as s:
            s.add(
                m.Event(
                    id=event.id,
                    user_id=event.user_id,
                    persona_id=event.persona_id,
                    convo_id=convo_id,
                    prev_event_id=event.prev_event_id,
                    role=event.role.value,
                    content=event.content,
                    salience=event.salience,
                    short_term_salience=event.short_term_salience,
                    emotional_intensity=event.emotional_intensity,
                    emotion_tags=list(event.emotion_tags),
                    embedding=embedding,
                    embedding_model=getattr(event, "_embedding_model", None),
                    family_id=event.family_id,
                    visibility=event.visibility,
                    participant_user_id=event.participant_user_id,
                )
            )
            await s.commit()

    async def list_events(
        self,
        *,
        user_id: str,
        persona_id: str,
        limit: int = 50,
        convo_id: str | None = None,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        from sqlalchemy import or_, select

        from ..db import models as m

        async with await self._session() as s:
            # Joint-session fix: in a shared family scope, admit ANY member's
            # shared row in family F — not just the requester's own rows — so
            # every member sees the whole shared thread. See ``_owns_or_shared``
            # for the in-memory twin and the security rationale (family_id is
            # pinned to the requester's family by ``_require_family_match``).
            if family_id is not None and visibility == "shared":
                ownership = or_(
                    m.Event.user_id == user_id,
                    (m.Event.family_id == family_id) & (m.Event.visibility == "shared"),
                )
            else:
                ownership = m.Event.user_id == user_id
            q = (
                select(m.Event)
                .where(ownership, m.Event.persona_id == persona_id)
                .order_by(m.Event.created_at.desc())
                .limit(limit)
            )
            if convo_id is not None:
                q = q.where(m.Event.convo_id == convo_id)
            if family_id is not None:
                if visibility == "shared":
                    q = q.where(m.Event.family_id == family_id, m.Event.visibility == "shared")
                elif visibility == "private":
                    q = q.where(
                        m.Event.family_id == family_id,
                        or_(
                            m.Event.visibility == "shared",
                            (m.Event.visibility == "private")
                            & (m.Event.participant_user_id == participant_user_id),
                        ),
                    )
            rows = (await s.execute(q)).scalars().all()
            return [_row_to_event(r) for r in reversed(rows)]

    async def recent_window(
        self,
        *,
        user_id: str,
        persona_id: str,
        convo_id: str,
        limit: int = 6,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        from sqlalchemy import or_, select

        from ..db import models as m

        async with await self._session() as s:
            # Joint-session fix: relax ``user_id`` ownership for the shared
            # scope so the therapist's window includes every member's shared
            # messages in the joint convo (see ``_owns_or_shared``).
            if family_id is not None and visibility == "shared":
                ownership = or_(
                    m.Event.user_id == user_id,
                    (m.Event.family_id == family_id) & (m.Event.visibility == "shared"),
                )
            else:
                ownership = m.Event.user_id == user_id
            q = select(m.Event).where(
                ownership,
                m.Event.persona_id == persona_id,
                m.Event.convo_id == convo_id,
            )
            # I9: defense-in-depth — filter by the turn's family scope so a
            # reused convo_id can't pull cross-scope events into the window.
            if family_id is not None and visibility == "shared":
                q = q.where(m.Event.family_id == family_id, m.Event.visibility == "shared")
            elif family_id is not None and visibility == "private":
                q = q.where(
                    m.Event.family_id == family_id,
                    or_(
                        m.Event.visibility == "shared",
                        (m.Event.visibility == "private")
                        & (m.Event.participant_user_id == participant_user_id),
                    ),
                )
            elif family_id is not None:
                q = q.where(m.Event.family_id == family_id)
            rows = (
                (await s.execute(q.order_by(m.Event.created_at.desc()).limit(limit)))
                .scalars()
                .all()
            )
            return [_row_to_event(r) for r in reversed(rows)]

    async def recall_candidates(
        self,
        *,
        user_id: str,
        persona_id: str,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[Event]:
        from sqlalchemy import or_, select

        from ..db import models as m

        donors = await self.list_donors(user_id=user_id, receiver_persona_id=persona_id)
        # Own scope: per-persona OR family-scoped. Family-scoped rows are
        # tagged with the persona_id they were minted under (typically "fam"
        # for the family therapist, but the column is just an opaque partition
        # key per PLAN §Family).
        persona_filters = [m.Event.persona_id == persona_id]
        if family_id and visibility:
            # The "own" rows in family scope are ones tagged with the active
            # persona. The donor's family rows could also carry different
            # persona_ids; they stay unioned unscoped below.
            persona_filters.append(m.Event.persona_id == persona_id)
        personas = [persona_id, *donors]
        async with await self._session() as s:
            # Joint-session fix: relax ``user_id`` ownership for the shared
            # scope so a joint recall probe sees every member's shared events
            # (see ``_owns_or_shared``).
            if family_id and visibility == "shared":
                ownership = or_(
                    m.Event.user_id == user_id,
                    (m.Event.family_id == family_id) & (m.Event.visibility == "shared"),
                )
            else:
                ownership = m.Event.user_id == user_id
            own_query = select(m.Event).where(ownership, m.Event.persona_id.in_(personas))
            if family_id and visibility == "shared":
                own_query = own_query.where(
                    m.Event.family_id == family_id, m.Event.visibility == "shared"
                )
            elif family_id and visibility == "private":
                own_query = own_query.where(
                    m.Event.family_id == family_id,
                    or_(
                        m.Event.visibility == "shared",
                        (m.Event.visibility == "private")
                        & (m.Event.participant_user_id == participant_user_id),
                    ),
                )
            # I6: order ascending by created_at so the recency score in
            # ``rank_and_chain`` (which keys off candidate list position) is
            # deterministic and matches the in-memory store's append order —
            # without this, Postgres returns rows in arbitrary order and
            # recency ranking is non-deterministic.
            own = (await s.execute(own_query.order_by(m.Event.created_at.asc()))).scalars().all()

            donor_rows: list = []
            if donors and (family_id is None or visibility is None):
                # Donors unioned unscoped (per-user MemoryShare semantics).
                donor_rows = (
                    (
                        await s.execute(
                            select(m.Event)
                            .where(m.Event.user_id == user_id, m.Event.persona_id.in_(donors))
                            .order_by(m.Event.created_at.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
            seen = {r.id for r in own}
            combined = list(own) + [r for r in donor_rows if r.id not in seen]
            return [_row_to_event(r) for r in combined]

    async def last_event_id(self, *, user_id: str, persona_id: str, convo_id: str) -> str | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.Event.id)
                    .where(
                        m.Event.user_id == user_id,
                        m.Event.persona_id == persona_id,
                        m.Event.convo_id == convo_id,
                    )
                    .order_by(m.Event.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row

    async def reinforce_events(
        self, *, user_id: str, event_ids: list[str], boost: float = 0.02
    ) -> None:
        from sqlalchemy import func, update

        from ..db import models as m

        if not event_ids:
            return
        async with await self._session() as s:
            await s.execute(
                update(m.Event)
                .where(m.Event.id.in_(event_ids), m.Event.user_id == user_id)
                .values(salience=func.least(1.0, m.Event.salience + boost))
            )
            await s.commit()

    async def list_conversations(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
    ) -> list[ConversationSummary]:
        from sqlalchemy import or_, select

        from ..db import models as m

        # Honest limit (K6 / I11): aggregate a user's events in Python rather
        # than a GROUP BY + array_agg query — same shape as the recall re-embed
        # path, keeps one code path with the in-memory store, and avoids
        # array_agg/aggregate_order_by dialect complexity. Beyond
        # ``_CONVO_SCAN_LIMIT`` older conversations drop off the list (the row
        # is ordered created_at DESC so the most recent convos survive).
        async with await self._session() as s:
            # Joint-session fix: relax ``user_id`` ownership for the shared
            # scope so the joint convo surfaces for every member with all
            # members' shared events grouped under it (see ``_owns_or_shared``).
            if family_id is not None and visibility == "shared":
                ownership = or_(
                    m.Event.user_id == user_id,
                    (m.Event.family_id == family_id) & (m.Event.visibility == "shared"),
                )
            else:
                ownership = m.Event.user_id == user_id
            q = (
                select(m.Event)
                .where(ownership)
                .order_by(m.Event.created_at.desc())
                .limit(_CONVO_SCAN_LIMIT)
            )
            if persona_id is not None:
                q = q.where(m.Event.persona_id == persona_id)
            if family_id is not None:
                if visibility == "shared":
                    q = q.where(m.Event.family_id == family_id, m.Event.visibility == "shared")
                elif visibility == "private":
                    q = q.where(
                        m.Event.family_id == family_id,
                        or_(
                            m.Event.visibility == "shared",
                            (m.Event.visibility == "private")
                            & (m.Event.participant_user_id == participant_user_id),
                        ),
                    )
                else:
                    q = q.where(m.Event.family_id == family_id)
            rows = (await s.execute(q)).scalars().all()
        # rows are created_at DESC; reverse so each convo's events ascend.
        convo_map: dict[str, list[tuple[Event, datetime]]] = {}
        for r in reversed(rows):
            convo_id = r.convo_id or ""
            if not convo_id:
                continue
            convo_map.setdefault(convo_id, []).append((_row_to_event(r), r.created_at))
        return _convo_summaries(convo_map, before=before, limit=limit)

    async def add_usage(self, usage: Usage) -> None:
        from ..db import models as m

        async with await self._session() as s:
            s.add(
                m.Usage(
                    id=usage.id,
                    user_id=usage.user_id,
                    family_id=usage.family_id,
                    provider_kind=usage.provider_kind.value
                    if hasattr(usage.provider_kind, "value")
                    else str(usage.provider_kind),
                    model=usage.model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=usage.cost_usd,
                )
            )
            await s.commit()

    async def list_usage(self, *, user_id: str) -> list[UsageRecord]:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(m.Usage)
                        .where(m.Usage.user_id == user_id)
                        .order_by(m.Usage.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                UsageRecord(
                    usage=Usage(
                        id=r.id,
                        user_id=r.user_id,
                        # CRITICAL: ``family_id`` was dropped here, which made
                        # the family budget gate see 0 family spend (rows looked
                        # personal) and the personal gate over-count (it could
                        # not tell family rows from personal). Persisted column
                        # is correct (add_usage writes family_id); the bug was
                        # only in this reconstruction. Restore it so the
                        # family_id==F / family_id IS NULL scoping works.
                        family_id=r.family_id,
                        provider_kind=r.provider_kind,
                        model=r.model,
                        prompt_tokens=int(r.prompt_tokens),
                        completion_tokens=int(r.completion_tokens),
                        cost_usd=float(r.cost_usd),
                    ),
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def list_usage_by_family(self, *, family_id: str) -> list[UsageRecord]:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(m.Usage)
                        .where(m.Usage.family_id == family_id)
                        .order_by(m.Usage.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [
                UsageRecord(
                    usage=Usage(
                        id=r.id,
                        user_id=r.user_id,
                        family_id=r.family_id,
                        provider_kind=r.provider_kind,
                        model=r.model,
                        prompt_tokens=int(r.prompt_tokens),
                        completion_tokens=int(r.completion_tokens),
                        cost_usd=float(r.cost_usd),
                    ),
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def recall_chains(
        self,
        *,
        user_id: str,
        persona_id: str,
        query: str,
        k: int = 3,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        embedder: object | None = None,
    ) -> list[EventChain]:
        emb = embedder or self._semantic
        # I11 / Phase 3a: ANN fast path. When a semantic embedder is active and
        # the corpus has enough vectors in ITS embedding space, prefilter the
        # candidates DB-side (HNSW ``<=>`` top-N) instead of scanning every
        # event. Any failure or a small corpus falls back to the exact scan.
        if emb is not None:
            try:
                chains = await self._recall_chains_ann(
                    emb,
                    user_id=user_id,
                    persona_id=persona_id,
                    query=query,
                    k=k,
                    family_id=family_id,
                    visibility=visibility,
                    participant_user_id=participant_user_id,
                )
                if chains is not None:
                    return chains
            except Exception:  # noqa: BLE001 — ANN is an optimization, never a dependency
                pass
        cands = await self.recall_candidates(
            user_id=user_id,
            persona_id=persona_id,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        return await _rank_chains(cands, query, k, semantic=emb)

    async def _recall_chains_ann(
        self,
        emb,  # type: ignore[no-untyped-def]
        *,
        user_id: str,
        persona_id: str,
        query: str,
        k: int,
        family_id: str | None,
        visibility: str | None,
        participant_user_id: str | None,
    ) -> list[EventChain] | None:
        """ANN prefilter: top ``_ANN_PREFILTER`` events by cosine distance in
        the embedder's OWN space (``embedding_model == emb.model`` — hash rows
        and other models' vectors are never compared), plus their chain parents
        (≤2 hops) so ``rank_and_chain`` can still walk intact chains. Returns
        ``None`` to fall back to the exact scan (small corpus, embed failure).

        Stored vectors are used ONLY for the prefilter; the actual scoring
        re-embeds query+contents through the same batched embedder (cache makes
        repeats cheap), so parents written before the semantic era rank in the
        same space as everything else.
        """
        from sqlalchemy import or_, select

        from ..db import models as m
        from .embeddings_semantic import rank_chains_semantic

        qv = await emb.embed_batch([query])
        if qv is None:
            return None
        donors = await self.list_donors(user_id=user_id, receiver_persona_id=persona_id)
        # Donor union follows recall_candidates: unscoped only outside family scope.
        personas = (
            [persona_id, *donors] if (family_id is None or visibility is None) else [persona_id]
        )
        sel = select(m.Event).where(
            m.Event.user_id == user_id,
            m.Event.persona_id.in_(personas),
            m.Event.embedding_model == emb.model,
        )
        if family_id and visibility == "shared":
            sel = sel.where(m.Event.family_id == family_id, m.Event.visibility == "shared")
        elif family_id and visibility == "private":
            sel = sel.where(
                m.Event.family_id == family_id,
                or_(
                    m.Event.visibility == "shared",
                    (m.Event.visibility == "private")
                    & (m.Event.participant_user_id == participant_user_id),
                ),
            )
        sel = sel.order_by(m.Event.embedding.cosine_distance(qv[0])).limit(_ANN_PREFILTER)
        async with await self._session() as s:
            rows = (await s.execute(sel)).scalars().all()
            if len(rows) < _ANN_MIN_ROWS:
                # Small corpus — the exact scan is comparably cheap AND covers
                # hash-vector rows this space-filtered query can't see.
                return None
            have = {r.id: r for r in rows}
            for _ in range(2):  # chain parents, ≤2 hops (rank_and_chain's walk depth)
                want = {
                    r.prev_event_id
                    for r in have.values()
                    if r.prev_event_id and r.prev_event_id not in have
                }
                if not want:
                    break
                parents = (
                    (
                        await s.execute(
                            select(m.Event).where(
                                m.Event.id.in_(want), m.Event.user_id == user_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not parents:
                    break
                for p in parents:
                    have[p.id] = p
            # P1: chain children, 1 hop — ``rank_and_chain`` now walks one hop
            # FORWARD from the seed (the aftermath of a salient moment), so the
            # prefiltered candidate set must include the seeds' children too.
            # Scoped like the main select (not relaxed like the parents fetch):
            # a child lookup by ``prev_event_id`` must not pull another
            # member's private row into the candidate pool.
            child_sel = select(m.Event).where(
                m.Event.user_id == user_id,
                m.Event.persona_id.in_(personas),
                m.Event.prev_event_id.in_(list(have.keys())),
                m.Event.id.notin_(list(have.keys())),
            )
            if family_id and visibility == "shared":
                child_sel = child_sel.where(
                    m.Event.family_id == family_id, m.Event.visibility == "shared"
                )
            elif family_id and visibility == "private":
                child_sel = child_sel.where(
                    m.Event.family_id == family_id,
                    or_(
                        m.Event.visibility == "shared",
                        (m.Event.visibility == "private")
                        & (m.Event.participant_user_id == participant_user_id),
                    ),
                )
            for c in (await s.execute(child_sel)).scalars().all():
                have.setdefault(c.id, c)
        ordered = sorted(have.values(), key=lambda r: r.created_at)
        cands = [_row_to_event(r) for r in ordered]
        return await rank_chains_semantic(emb, cands, query, k)

    async def table_exists(self, name: str = "events") -> bool:
        """True if the given table exists in the public schema. Used at startup
        to detect that ``alembic upgrade head`` actually applied — if it didn't,
        ``make_store`` falls back to the in-memory store so the app degrades
        gracefully (session-scoped memory) instead of 500ing on every query."""
        from sqlalchemy import text  # lazy

        try:
            async with await self._session() as s:
                val = (await s.execute(text(f"SELECT to_regclass('public.{name}')"))).scalar()
            return val is not None
        except Exception:
            return False

    async def add_memory(self, memory: Memory) -> None:
        from ..db import models as m  # lazy

        async with await self._session() as s:
            s.add(
                m.Memory(
                    id=memory.id,
                    user_id=memory.user_id,
                    persona_id=memory.persona_id,
                    content=memory.content,
                    tags=list(memory.tags),
                    salience=memory.salience,
                    source_event_ids=list(memory.source_event_ids),
                    status=memory.status.value,
                    family_id=memory.family_id,
                    visibility=memory.visibility,
                    participant_user_id=memory.participant_user_id,
                )
            )
            await s.commit()

    async def list_memories(
        self,
        *,
        user_id: str,
        persona_id: str,
        include_donors: bool = True,
        family_id: str | None = None,
        visibility: str | None = None,
        participant_user_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Memory]:
        from sqlalchemy import or_, select

        from ..db import models as m

        if include_donors:
            donors = await self.list_donors(user_id=user_id, receiver_persona_id=persona_id)
            personas = [persona_id, *donors]
        else:
            personas = [persona_id]
            donors = []
        statuses = ["active", "superseded"] if include_superseded else ["active"]
        async with await self._session() as s:
            own_query = select(m.Memory).where(
                m.Memory.user_id == user_id,
                m.Memory.persona_id.in_(personas),
                m.Memory.status.in_(statuses),
            )
            if family_id and visibility == "shared":
                own_query = own_query.where(
                    m.Memory.family_id == family_id, m.Memory.visibility == "shared"
                )
            elif family_id and visibility == "private":
                own_query = own_query.where(
                    m.Memory.family_id == family_id,
                    or_(
                        m.Memory.visibility == "shared",
                        (m.Memory.visibility == "private")
                        & (m.Memory.participant_user_id == participant_user_id),
                    ),
                )
            own = (await s.execute(own_query)).scalars().all()
            donor_rows: list = []
            if donors and (family_id is None or visibility is None):
                donor_rows = (
                    (
                        await s.execute(
                            select(m.Memory).where(
                                m.Memory.user_id == user_id,
                                m.Memory.persona_id.in_(donors),
                                m.Memory.status == "active",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            seen = {r.id for r in own}
            combined = list(own) + [r for r in donor_rows if r.id not in seen]
            combined.sort(key=lambda r: (r.salience, r.updated_at), reverse=True)
            return [_row_to_memory(r) for r in combined]

    async def update_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        content: str,
        tags: list[str],
        salience: float,
        source_event_ids: list[str],
        family_id: str | None = None,
    ) -> None:
        from sqlalchemy import select

        from ..db import models as m

        # I10 / M1.1: re-scope to the caller in the WHERE clause — a memory id
        # from another user/persona/family is not fetched and not mutated.
        scope = [
            m.Memory.id == memory_id,
            m.Memory.user_id == user_id,
            m.Memory.persona_id == persona_id,
        ]
        if family_id is not None:
            scope.append(m.Memory.family_id == family_id)
        async with await self._session() as s:
            row = (await s.execute(select(m.Memory).where(*scope))).scalar_one_or_none()
            if row is None:
                return
            merged = list(dict.fromkeys([*row.source_event_ids, *source_event_ids]))
            row.content = content
            row.tags = tags
            row.salience = salience
            row.source_event_ids = merged
            row.updated_at = datetime.now(UTC)
            await s.commit()

    async def supersede_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        persona_id: str,
        superseded_by: str | None = None,
        family_id: str | None = None,
    ) -> None:
        from sqlalchemy import update

        from ..db import models as m

        scope = [
            m.Memory.id == memory_id,
            m.Memory.user_id == user_id,
            m.Memory.persona_id == persona_id,
            m.Memory.status == "active",
        ]
        if family_id is not None:
            scope.append(m.Memory.family_id == family_id)
        async with await self._session() as s:
            await s.execute(
                update(m.Memory)
                .where(*scope)
                .values(
                    status="superseded", superseded_by=superseded_by, updated_at=datetime.now(UTC)
                )
            )
            await s.commit()

    # --- cross-persona live memory shares ---

    async def add_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> MemoryShare:
        from sqlalchemy import select

        from ..db import models as m

        if donor_persona_id == receiver_persona_id:
            raise ValueError("cannot share a persona's memory with itself")
        async with await self._session() as s:
            existing = (
                await s.execute(
                    select(m.MemoryShare).where(
                        m.MemoryShare.user_id == user_id,
                        m.MemoryShare.donor_persona_id == donor_persona_id,
                        m.MemoryShare.receiver_persona_id == receiver_persona_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _row_to_memoryshare(existing)  # idempotent
            row = m.MemoryShare(
                id=_new_id(),
                user_id=user_id,
                donor_persona_id=donor_persona_id,
                receiver_persona_id=receiver_persona_id,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_memoryshare(row)

    async def remove_share(
        self, *, user_id: str, donor_persona_id: str, receiver_persona_id: str
    ) -> None:
        from sqlalchemy import delete

        from ..db import models as m

        async with await self._session() as s:
            await s.execute(
                delete(m.MemoryShare).where(
                    m.MemoryShare.user_id == user_id,
                    m.MemoryShare.donor_persona_id == donor_persona_id,
                    m.MemoryShare.receiver_persona_id == receiver_persona_id,
                )
            )
            await s.commit()

    async def list_shares(self, *, user_id: str, donor_persona_id: str) -> list[MemoryShare]:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(m.MemoryShare)
                        .where(
                            m.MemoryShare.user_id == user_id,
                            m.MemoryShare.donor_persona_id == donor_persona_id,
                        )
                        .order_by(m.MemoryShare.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_to_memoryshare(r) for r in rows]

    async def list_donors(self, *, user_id: str, receiver_persona_id: str) -> list[str]:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(m.MemoryShare.donor_persona_id).where(
                            m.MemoryShare.user_id == user_id,
                            m.MemoryShare.receiver_persona_id == receiver_persona_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    # --- reset: per-convo event delete + full persona memory wipe ---

    async def delete_convo_events(self, *, user_id: str, persona_id: str, convo_id: str) -> int:
        from sqlalchemy import delete

        from ..db import models as m

        async with await self._session() as s:
            result = await s.execute(
                delete(m.Event).where(
                    m.Event.user_id == user_id,
                    m.Event.persona_id == persona_id,
                    m.Event.convo_id == convo_id,
                )
            )
            await s.commit()
            return int(result.rowcount or 0)

    async def wipe_persona_memory(self, *, user_id: str, persona_id: str) -> None:
        # One session, three deletes, single commit. Memories of every status
        # are removed (superseded rows too). Outgoing donor shares go (the
        # persona has nothing left to share); incoming shares stay (donor-owned
        # by other personas).
        from sqlalchemy import delete

        from ..db import models as m

        async with await self._session() as s:
            await s.execute(
                delete(m.Event).where(m.Event.user_id == user_id, m.Event.persona_id == persona_id)
            )
            await s.execute(
                delete(m.Memory).where(
                    m.Memory.user_id == user_id, m.Memory.persona_id == persona_id
                )
            )
            await s.execute(
                delete(m.MemoryShare).where(
                    m.MemoryShare.user_id == user_id,
                    m.MemoryShare.donor_persona_id == persona_id,
                )
            )
            await s.commit()

    # --- family-scope wipes (Postgres) ---
    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None:
        from sqlalchemy import delete

        from ..db import models as m

        async with await self._session() as s:
            await s.execute(
                delete(m.Event).where(
                    m.Event.family_id == family_id,
                    m.Event.user_id == user_id,
                    m.Event.visibility == "private",
                    m.Event.participant_user_id == user_id,
                )
            )
            await s.execute(
                delete(m.Memory).where(
                    m.Memory.family_id == family_id,
                    m.Memory.user_id == user_id,
                    m.Memory.visibility == "private",
                    m.Memory.participant_user_id == user_id,
                )
            )
            await s.execute(
                delete(m.JournalEntry).where(
                    m.JournalEntry.family_id == family_id,
                    m.JournalEntry.user_id == user_id,
                    m.JournalEntry.visibility == "private",
                    m.JournalEntry.participant_user_id == user_id,
                )
            )
            await s.commit()

    async def wipe_family_scope(self, *, family_id: str) -> None:
        from sqlalchemy import delete

        from ..db import models as m

        async with await self._session() as s:
            await s.execute(delete(m.Event).where(m.Event.family_id == family_id))
            await s.execute(delete(m.Memory).where(m.Memory.family_id == family_id))
            await s.execute(delete(m.JournalEntry).where(m.JournalEntry.family_id == family_id))
            await s.execute(delete(m.Usage).where(m.Usage.family_id == family_id))
            await s.commit()

    # --- providers (BYOK metadata + zero-knowledge enc_blob at-rest backup) ---

    async def add_provider(
        self, provider: Provider, *, api_key_ciphertext: str | None = None
    ) -> Provider:
        from ..db import models as m  # lazy

        async with await self._session() as s:
            row = m.Provider(
                id=provider.id,
                user_id=provider.user_id,
                kind=provider.kind.value,
                label=provider.label,
                base_url=provider.base_url,
                key_handle=provider.key_handle,
                model=provider.model,
                embeddings_model=provider.embeddings_model,
                enc_blob=provider.enc_blob,
                api_key_ciphertext=api_key_ciphertext,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_provider(row)

    async def list_providers(self, *, user_id: str) -> list[Provider]:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            rows = (
                (await s.execute(select(m.Provider).where(m.Provider.user_id == user_id)))
                .scalars()
                .all()
            )
            return [_row_to_provider(r) for r in rows]

    async def get_provider(self, *, user_id: str, provider_id: str) -> Provider | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.Provider).where(
                        m.Provider.user_id == user_id, m.Provider.id == provider_id
                    )
                )
            ).scalar_one_or_none()
            return _row_to_provider(row) if row is not None else None

    async def get_provider_api_key_ciphertext(
        self, *, user_id: str, key_handle: str
    ) -> str | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.Provider.api_key_ciphertext)
                    .where(
                        m.Provider.user_id == user_id,
                        m.Provider.key_handle == key_handle,
                    )
                    .order_by(m.Provider.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row if row is not None else None

    async def delete_provider(self, *, user_id: str, provider_id: str) -> bool:
        from sqlalchemy import delete, select

        from ..db import models as m

        async with await self._session() as s:
            existing = (
                await s.execute(
                    select(m.Provider.id).where(
                        m.Provider.user_id == user_id, m.Provider.id == provider_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            await s.execute(
                delete(m.Provider).where(
                    m.Provider.user_id == user_id, m.Provider.id == provider_id
                )
            )
            await s.commit()
            return True

    async def update_provider(
        self,
        *,
        user_id: str,
        provider_id: str,
        label: str,
        base_url: str | None,
        model: str | None,
        embeddings_model: str | None = None,
    ) -> Provider | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.Provider).where(
                        m.Provider.user_id == user_id, m.Provider.id == provider_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.label = label
            row.base_url = base_url
            row.model = model
            row.embeddings_model = embeddings_model
            await s.commit()
            await s.refresh(row)
            return _row_to_provider(row)

    # --- journal (user-authored diary entries) ---

    async def add_journal_entry(
        self,
        *,
        user_id: str,
        persona_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
        salience: float,
        source_convo_id: str | None,
        source_event_id: str | None,
        family_id: str | None = None,
        visibility: str = "private",
        participant_user_id: str | None = None,
    ) -> JournalEntry:
        from ..db import models as m  # lazy

        async with await self._session() as s:
            row = m.JournalEntry(
                id=_new_id(),
                user_id=user_id,
                persona_id=persona_id,
                title=title,
                body=body,
                mood=mood,
                tags=list(tags),
                salience=salience,
                source_convo_id=source_convo_id,
                source_event_id=source_event_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_journal_entry(row)

    async def list_journal_entries(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        q: str | None = None,
        tag: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        family_id: str | None = None,
    ) -> list[JournalEntry]:
        from sqlalchemy import select

        from ..db import models as m

        stmt = select(m.JournalEntry).where(m.JournalEntry.user_id == user_id)
        if persona_id is not None:
            stmt = stmt.where(m.JournalEntry.persona_id == persona_id)
        if family_id is not None:
            stmt = stmt.where(m.JournalEntry.family_id == family_id)
        if mood is not None:
            stmt = stmt.where(m.JournalEntry.mood == mood)
        if tag is not None:
            # JSONB @> containment: tags array contains ``tag``. Server-side so
            # pagination stays correct (a client-side tag filter would miss
            # matches on later pages).
            stmt = stmt.where(m.JournalEntry.tags.contains([tag]))
        if from_dt is not None:
            stmt = stmt.where(m.JournalEntry.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(m.JournalEntry.created_at <= to_dt)
        if q:
            # ILIKE = case-insensitive substring, works for RU and EN. Match
            # against either the body or the optional title.
            like = f"%{q}%"
            stmt = stmt.where(m.JournalEntry.body.ilike(like) | m.JournalEntry.title.ilike(like))
        stmt = stmt.order_by(m.JournalEntry.created_at.desc()).limit(limit).offset(offset)
        async with await self._session() as s:
            rows = (await s.execute(stmt)).scalars().all()
            return [_row_to_journal_entry(r) for r in rows]

    async def list_journal_tags(
        self,
        *,
        user_id: str,
        persona_id: str | None = None,
        mood: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        family_id: str | None = None,
    ) -> list[str]:
        # Aggregate over the same scope as ``list_journal_entries`` (minus
        # ``tag``/``q``/pagination). Pulls the JSONB ``tags`` column and
        # dedups in Python — a single round-trip with the existing
        # ``(user_id, created_at)`` index is enough for the row counts this
        # surface sees; if the tag set ever grows large we'll add a GIN on
        # ``tags`` and push the dedup into SQL. Sorted for a stable UI.
        from sqlalchemy import select

        from ..db import models as m

        if not user_id:
            return []
        stmt = select(m.JournalEntry.tags).where(m.JournalEntry.user_id == user_id)
        if persona_id is not None:
            stmt = stmt.where(m.JournalEntry.persona_id == persona_id)
        if family_id is not None:
            stmt = stmt.where(m.JournalEntry.family_id == family_id)
        if mood is not None:
            stmt = stmt.where(m.JournalEntry.mood == mood)
        if from_dt is not None:
            stmt = stmt.where(m.JournalEntry.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(m.JournalEntry.created_at <= to_dt)
        async with await self._session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        seen: set[str] = set()
        for tags in rows:
            for t in tags or []:
                if t:
                    seen.add(t)
        return sorted(seen)

    async def get_journal_entry(self, *, user_id: str, entry_id: str) -> JournalEntry | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.JournalEntry).where(
                        m.JournalEntry.user_id == user_id, m.JournalEntry.id == entry_id
                    )
                )
            ).scalar_one_or_none()
            return _row_to_journal_entry(row) if row is not None else None

    async def update_journal_entry(
        self,
        *,
        user_id: str,
        entry_id: str,
        title: str | None,
        body: str,
        mood: str | None,
        tags: list[str],
    ) -> JournalEntry | None:
        from sqlalchemy import select

        from ..db import models as m

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(m.JournalEntry).where(
                        m.JournalEntry.user_id == user_id, m.JournalEntry.id == entry_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.title = title
            row.body = body
            row.mood = mood
            row.tags = list(tags)
            row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return _row_to_journal_entry(row)

    async def delete_journal_entry(self, *, user_id: str, entry_id: str) -> bool:
        from sqlalchemy import delete, select

        from ..db import models as m

        async with await self._session() as s:
            existing = (
                await s.execute(
                    select(m.JournalEntry.id).where(
                        m.JournalEntry.user_id == user_id, m.JournalEntry.id == entry_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            await s.execute(
                delete(m.JournalEntry).where(
                    m.JournalEntry.user_id == user_id, m.JournalEntry.id == entry_id
                )
            )
            await s.commit()
            return True


# --- factory -----------------------------------------------------------------


def make_store(settings: Settings) -> MemoryStore:
    """Pick Postgres when ``COMPANION_USE_DB=1``, else in-memory. Postgres is
    constructed but only connects on first use, so a unreachable DB doesn't
    break startup; per-call failures surface to the caller (router) which
    already redacts. The semantic embedder (EMBEDDINGS_MODE=semantic) is
    attached here so both store impls share one instance (one LRU cache)."""
    from .embeddings_semantic import make_semantic_embedder  # lazy — avoids a cycle

    semantic = make_semantic_embedder(settings)
    if settings.use_db:
        return PostgresStore(settings, semantic_embedder=semantic)  # type: ignore[return-value]
    return InMemoryStore(semantic_embedder=semantic)  # type: ignore[return-value]


# --- helpers -----------------------------------------------------------------


async def _rank_chains(
    cands: list[Event], query: str, k: int, *, semantic
) -> list[EventChain]:  # type: ignore[no-untyped-def]
    """Shared recall ranking: try the semantic path when an embedder is
    attached, fall back to the pure hash-embedder ``rank_and_chain`` on any
    embedding failure (recall must never break a turn)."""
    if semantic is not None:
        from .embeddings_semantic import rank_chains_semantic  # lazy — avoids a cycle

        chains = await rank_chains_semantic(semantic, cands, query, k)
        if chains is not None:
            return chains
    return rank_and_chain(cands, query, k)


def _convo(e: Event) -> str:
    # InMemoryStore events carry convo_id on a private attr set by event_chain.
    return getattr(e, "_convo_id", "") or ""


# K6: conversation-list projection helpers. There is no ``conversations`` table
# — the list is derived from ``events`` grouped by ``convo_id``. ``_TITLE_MAX``
# / ``_PREVIEW_MAX`` keep the drawer rows bounded; truncation collapses
# newlines so a multi-line first message still renders as one line. Honest
# limit: this aggregates a user's events in Python (same shape as the I11
# re-embed path); beyond ``_CONVO_SCAN_LIMIT`` older conversations drop off
# the list — acceptable for MVP, like the exact-cosine-until-50k rule.
_TITLE_MAX = 60
_PREVIEW_MAX = 140
_CONVO_SCAN_LIMIT = 5000

# I11 / Phase 3a: ANN prefilter sizing. Top-N candidates fetched DB-side by
# cosine distance; below _ANN_MIN_ROWS the exact scan is comparably cheap (and
# also covers legacy hash-vector rows), so the fast path steps aside.
_ANN_PREFILTER = 200
_ANN_MIN_ROWS = 50


def _truncate(text: str, n: int) -> str:
    cleaned = (text or "").strip().replace("\n", " ")
    if len(cleaned) <= n:
        return cleaned
    return cleaned[: n - 1].rstrip() + "…"


def _summarize_convo(
    convo_id: str,
    events_with_ts: list[tuple[Event, datetime]],
) -> ConversationSummary | None:
    """Build a ``ConversationSummary`` from a convo's events (ascending by ts).

    ``events_with_ts`` must be non-empty and ordered by timestamp ascending.
    ``title`` is the first user-role message (falling back to the first event);
    ``preview`` is the last event's content; family scope is taken from the
    last event so a convo re-scoped mid-thread reflects its current scope.
    """
    if not events_with_ts:
        return None
    events = [e for e, _ in events_with_ts]
    created_at = events_with_ts[0][1]
    last_activity = events_with_ts[-1][1]
    persona_id = events[0].persona_id
    title_src = next((e for e in events if e.role == EventRole.user), events[0])
    last = events[-1]
    return ConversationSummary(
        convo_id=convo_id,
        persona_id=persona_id,
        title=_truncate(title_src.content, _TITLE_MAX),
        preview=_truncate(last.content, _PREVIEW_MAX),
        event_count=len(events),
        created_at=created_at,
        last_activity=last_activity,
        family_id=getattr(last, "family_id", None),
        visibility=getattr(last, "visibility", "private") or "private",
    )


def _convo_summaries(
    convo_map: dict[str, list[tuple[Event, datetime]]],
    *,
    before: datetime | None,
    limit: int,
) -> list[ConversationSummary]:
    """Build, cursor-filter, and order summaries from a convo→events map."""
    summaries: list[ConversationSummary] = []
    for convo_id, evts in convo_map.items():
        s = _summarize_convo(convo_id, evts)
        if s is not None:
            summaries.append(s)
    summaries.sort(key=lambda c: c.last_activity, reverse=True)
    if before is not None:
        summaries = [c for c in summaries if c.last_activity < before]
    return summaries[:limit]


def _row_to_event(row) -> Event:  # type: ignore[no-untyped-def]
    return Event(
        id=row.id,
        user_id=row.user_id,
        persona_id=row.persona_id,
        prev_event_id=row.prev_event_id,
        role=EventRole(row.role),
        content=row.content,
        salience=float(row.salience),
        short_term_salience=float(getattr(row, "short_term_salience", 0.0) or 0.0),
        emotional_intensity=float(getattr(row, "emotional_intensity", 0.0) or 0.0),
        emotion_tags=list(row.emotion_tags or []),
        created_at=getattr(row, "created_at", None),
        family_id=row.family_id,
        visibility=getattr(row, "visibility", "private") or "private",
        participant_user_id=row.participant_user_id,
    )


def _row_to_memory(row) -> Memory:  # type: ignore[no-untyped-def]
    return Memory(
        id=row.id,
        user_id=row.user_id,
        persona_id=row.persona_id,
        content=row.content,
        tags=list(row.tags or []),
        salience=float(row.salience),
        source_event_ids=list(row.source_event_ids or []),
        status=MemoryStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        family_id=row.family_id,
        visibility=getattr(row, "visibility", "private") or "private",
        participant_user_id=row.participant_user_id,
    )


def _row_to_memoryshare(row) -> MemoryShare:  # type: ignore[no-untyped-def]
    return MemoryShare(
        id=row.id,
        user_id=row.user_id,
        donor_persona_id=row.donor_persona_id,
        receiver_persona_id=row.receiver_persona_id,
        created_at=row.created_at,
    )


def _row_to_provider(row) -> Provider:  # type: ignore[no-untyped-def]
    return Provider(
        id=row.id,
        user_id=row.user_id,
        kind=ProviderKind(row.kind),
        label=row.label,
        base_url=row.base_url,
        key_handle=row.key_handle,
        model=row.model,
        embeddings_model=getattr(row, "embeddings_model", None),
        enc_blob=row.enc_blob,
    )


def _row_to_journal_entry(row) -> JournalEntry:  # type: ignore[no-untyped-def]
    return JournalEntry(
        id=row.id,
        user_id=row.user_id,
        persona_id=row.persona_id,
        title=row.title,
        body=row.body,
        mood=row.mood,
        tags=list(row.tags or []),
        salience=float(row.salience),
        source_convo_id=row.source_convo_id,
        source_event_id=row.source_event_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        family_id=row.family_id,
        visibility=getattr(row, "visibility", "private") or "private",
        participant_user_id=row.participant_user_id,
    )


__all__ = [
    "InMemoryStore",
    "MemoryStore",
    "PostgresStore",
    "UsageRecord",
    "make_store",
]
