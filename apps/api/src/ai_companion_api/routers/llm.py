"""``POST /v1/llm/stream`` — the streaming chat endpoint.

SSE event stream (each ``data:`` line is a JSON object with a ``type`` field;
the union is defined in ``@ai-companion/contracts``)::

    session → token (×N) → optional fallback → usage → done
    (mid-stream ``error`` with a redacted message on unrecoverable failure)

Security: the BYOK key (if any) is decrypted from ``enc_key_blob`` (personal)
OR ``family_enc_key_blob`` (family) with the server session private key, held
on the BYOK chain candidate, and zeroized *after* the whole fallback chain
runs — even if BYOK failed and a later candidate served the turn. Errors
carry no key material; every surfaced string passes through ``redact``.

Personal and family blobs are mutually exclusive on the wire: sending both
yields 400. Family turns use the family key + family budget; personal turns
use the personal key + personal budget. The family key is owned by the
family owner and shared with members via a zero-knowledge family vault
(separate from the personal vault). The family passphrase never enters this
endpoint — it stays in the browser.

Phase 3: when ``memory_on``, the context is built from the event store —
``[persona_block, salient_chains(recall, family-scoped), recent_window,
current_msg]`` — and after the turn completes we append the user + assistant
events (linked via ``prev_event_id``, tagged with the family scope) and a
usage row (also family-tagged when ``family_id`` is set). Memory writes
never break the stream.

Phase 4: the turn walks a real fallback chain (BYOK → env → Ollama → mock) via
``run_with_fallback``; on a provider failure (429/5xx/timeout) a ``fallback``
event is emitted and the next candidate is tried. The monthly budget is
checked before the chain runs: at hard-stop (≥100% of cap) the real providers
are skipped and the turn falls through to mock with a single ``fallback``
event (reason ``"budget hard-stop"``); at soft-warn (≥80%) the turn proceeds
normally and the dashboard surfaces the warning. Family turns roll up spend
against the family budget (per ``family_id``); personal turns roll up
against the personal budget (per ``user_id``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from ai_companion_contracts import (
    Event,
    EventRole,
    LlmStreamRequest,
    Memory,
    MemoryStatus,
    Principal,
    Usage,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from ..crypto.envelope import EnvelopeCipher, EnvelopeDecryptError
from ..deps import (
    get_current_principal,
    get_current_user_id,
    get_session_ecdh,
    get_settings,
    get_store,
)
from ..llm import ProviderResolutionError, build_chain
from ..llm.provider import utility_model_for
from ..memory import adaptive, append_event, build_context, chains_to_messages
from ..memory.consolidate import maybe_consolidate, maybe_consolidate_eras
from ..memory.embeddings_semantic import (
    SemanticEmbedder,
    make_semantic_embedder,
    rank_memories_semantic,
)
from ..memory.extract import MemoryOp, extract_memories
from ..memory.recall import memories_to_message, open_loops_message, rank_memories
from ..memory.relationship import (
    NOTE_TAG,
    maybe_update_relationship_note,
    relationship_message,
)
from ..memory.salience import SalienceScore
from ..memory.salience_llm import judge_salience
from ..memory.session_bridge import build_session_bridge
from ..memory.store import MemoryStore
from ..observability import redact
from ..ratelimit import limiter, user_or_ip_key
from ..routing import compute_budget, record_fallback, run_with_fallback
from ..routing.entitlement import is_paid_subscriber
from ..safety import screen_assistant_text, screen_user_message
from ..vault.decrypt import DecryptedKey, DecryptError, parse_decrypted_key
from ..vault.zeroize import zeroized

logger = logging.getLogger(__name__)

# Hoist the FastAPI dependencies to module-level ``Annotated`` aliases so the
# ``Depends(...)`` call isn't an in-signature default — ruff B008 allowlists
# ``fastapi.Depends`` but a plain ``x: T = Depends(...)`` default still trips it
# for the principal dep. Same idiom as ``UserId`` / ``Store`` in memory.py and
# ``FromQuery`` in journal.py.
UserIdDep = Annotated[str, Depends(get_current_user_id)]
PrincipalDep = Annotated[Principal, Depends(get_current_principal)]

# Extract atomic memories only when the user's message is salient enough to be
# worth an extra LLM call. Trivial turns ("hi", "ok", "yeah") skip extraction.
EXTRACT_SALIENCE_THRESHOLD = 0.3

# P1 (query expansion): how many trailing user messages join the retrieval
# query, and how much of each — a short "да, наверное" retrieves garbage alone;
# with the preceding user messages the query carries the actual topic.
_QUERY_EXPANSION_MSGS = 2
_QUERY_EXPANSION_CHARS = 200

# I8: in-process turn idempotency. A client that retries a turn (e.g. after a
# connection drop) used to duplicate the user+assistant events and fork the
# event chain. When the client sends a ``request_id`` we dedup by
# ``(user_id, convo_id, request_id)``: the first attempt reserves the key on
# entry and marks it done after persist; a concurrent or later retry with the
# same key skips persistence (events + usage + memory ops) so the chain isn't
# duplicated. The stream still re-runs (the client lost the first response and
# needs a fresh one) — only the side effects are deduped. In-process only, like
# ``fallback_last_turn``; lost on restart (an acceptable MVP limit — a retry
# after a server restart would re-persist, but that is rare and idempotent
# enough at the chain level via the per-convo append lock, I7).
#
# ``_idem_done`` is FIFO-bounded: without a cap every request_id ever served
# would stay in process memory forever. Retries arrive within seconds/minutes
# of the original, so evicting the oldest keys once the set is thousands deep
# loses nothing in practice.
_IDEM_DONE_MAX = 4096
_idem_inflight: set[tuple[str, str, str]] = set()
_idem_done: OrderedDict[tuple[str, str, str], None] = OrderedDict()


def _idem_mark_done(key: tuple[str, str, str]) -> None:
    """Release the in-flight reservation and record the key as done (bounded)."""
    _idem_inflight.discard(key)
    _idem_done[key] = None
    _idem_done.move_to_end(key)
    while len(_idem_done) > _IDEM_DONE_MAX:
        _idem_done.popitem(last=False)

router = APIRouter()


def _evt(payload: dict) -> dict:
    return {"data": json.dumps(payload, separators=(",", ":"))}


def _resolve_served(served: object | None, cands: list) -> tuple[object | None, object, str, str]:  # type: ignore[type-arg]
    """Pick the candidate that served the turn (or the mock fallback) and its
    usage. Returns (served_cand, usage, usage_kind, usage_model)."""
    if served is not None:
        return (
            served,
            served.adapter.last_usage(),  # type: ignore[attr-defined]
            served.kind,  # type: ignore[attr-defined]
            served.model,  # type: ignore[attr-defined]
        )
    # No candidate reported serving (e.g. an empty chain) — report mock-zero.
    return None, cands[-1].adapter.last_usage(), "mock", "mock"


def _usage_evt(u: object, usage_kind: str, usage_model: str) -> dict:
    return {
        "type": "usage",
        "provider_kind": usage_kind,
        "model": usage_model,
        "prompt_tokens": u.prompt_tokens,  # type: ignore[attr-defined]
        "completion_tokens": u.completion_tokens,  # type: ignore[attr-defined]
        "cost_usd": u.cost_usd,  # type: ignore[attr-defined]
    }


def _events_to_window(
    events: list, family_members: dict[str, str] | None = None
) -> list[dict[str, str]]:  # type: ignore[type-arg]
    """Render recent events as chat messages (drop the system role). In a
    family session, user-role events with a mapped ``participant_user_id`` are
    rendered as ``"{name}: {content}"`` so the family therapist can tell who
    said what in a joint session. Assistant / unmapped rows render plain."""
    fm = family_members or {}
    out: list[dict[str, str]] = []
    for e in events:
        role = e.role.value if hasattr(e.role, "value") else str(e.role)
        if role not in ("user", "assistant"):
            continue
        content = e.content
        if role == "user" and fm and e.participant_user_id and e.participant_user_id in fm:
            content = f"{fm[e.participant_user_id]}: {content}"
        out.append({"role": role, "content": content})
    return out


async def _resolve_byok_from_envelope(
    *,
    request: Request,
    store: MemoryStore,
    user_id: str,
    family_id: str | None,
    key_handle: str | None,
) -> DecryptedKey | None:
    """Server-side envelope fallback for the per-turn BYOK key.

    When the new client sends no ``enc_key_blob`` (``None``), the server
    resolves the active provider's ``api_key_ciphertext`` from its envelope
    store, decrypts it under ``MESSENGER_TOKEN_DEK``, and parses the plaintext
    JSON into a ``DecryptedKey``. The caller zeroizes ``dk.api_key`` after the
    chain runs (same ``zeroized()`` window as the per-turn blob path).

    Family turns have two resolution modes, selected by the family row's
    ``use_owner_personal_key`` flag (owner-only, set via PUT /v1/family/
    owner-personal-key): when the flag is on, the ``key_handle`` is looked up
    against the OWNER's personal ``providers`` row (``store.get_provider_
    api_key_ciphertext(user_id=fam.owner_user_id, ...)``) — the owner shares
    their personal key with the family without re-entering it. When off (the
    default), the family store is used as before. The owner's user_id comes
    from the family record, never from the client, so a member cannot retarget
    the lookup. Honest disclosure: this is NOT zero-knowledge — the server can
    decrypt the owner's key in memory for a member's turn and zeroizes after;
    the member never sees the key (same model as family keys).

    Returns ``None`` when no envelope is configured, no ``key_handle`` was
    supplied, or no provider row matches — the chain then falls through to the
    env ladder / mock (no 500). A tampered/corrupted ciphertext raises
    ``EnvelopeDecryptError`` which the caller catches and logs (turn degrades to
    fallback/mock, never 500). Cross-user scoping is enforced by the store
    (``user_id`` for personal, ``family_id`` for family) — a stranger's row is
    invisible (404-equivalent: ``None``).
    """
    if key_handle is None:
        return None
    envelope: EnvelopeCipher | None = getattr(request.app.state, "envelope", None)
    if envelope is None:
        return None
    ciphertext: str | None
    if family_id is not None:
        family_store = getattr(request.app.state, "family_store", None)
        if family_store is None:
            return None
        # Owner-personal-key mode: the family's flag redirects the lookup to
        # the owner's personal providers row. Best-effort fetch — a store
        # failure falls through to the default family-store path below so a
        # transient error can't 500 the turn (it just won't use the personal
        # key this turn). The owner is resolved from the family record, never
        # from the client.
        use_owner_key = False
        owner_user_id: str | None = None
        try:
            fam = await family_store.get_family(family_id=family_id)
            if fam is not None:
                use_owner_key = fam.use_owner_personal_key
                owner_user_id = fam.owner_user_id
        except Exception as exc:  # noqa: BLE001 — fetch failure must not 500
            logger.warning(
                "family BYOK owner-mode lookup failed: %s: %s", type(exc).__name__, exc
            )
        if use_owner_key and owner_user_id is not None:
            try:
                ciphertext = await store.get_provider_api_key_ciphertext(
                    user_id=owner_user_id, key_handle=key_handle
                )
            except Exception as exc:  # noqa: BLE001 — store failure must not 500
                logger.warning(
                    "family BYOK owner-personal-key lookup failed: %s: %s",
                    type(exc).__name__,
                    exc,
                )
                return None
        else:
            try:
                ciphertext = await family_store.get_family_provider_api_key_ciphertext(
                    family_id=family_id, key_handle=key_handle
                )
            except Exception as exc:  # noqa: BLE001 — store failure must not 500
                logger.warning("family BYOK envelope lookup failed: %s: %s", type(exc).__name__, exc)
                return None
    else:
        try:
            ciphertext = await store.get_provider_api_key_ciphertext(
                user_id=user_id, key_handle=key_handle
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("personal BYOK envelope lookup failed: %s: %s", type(exc).__name__, exc)
            return None
    if ciphertext is None:
        return None
    try:
        plaintext = envelope.decrypt_b64(ciphertext)
    except EnvelopeDecryptError as exc:
        logger.warning("BYOK envelope decrypt failed (tampered/wrong DEK): %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — never 500 over a bad blob
        logger.warning("BYOK envelope decrypt failed: %s: %s", type(exc).__name__, exc)
        return None
    try:
        dk = parse_decrypted_key(plaintext)
    except DecryptError as exc:
        logger.warning("BYOK envelope plaintext malformed: %s", exc)
        return None
    return dk


# P0 #4 / P1 renderers (session bridge, relationship note, open loops) live in
# the memory package — ``memory/session_bridge.py``, ``memory/relationship.py``,
# ``memory/recall.py`` — so the eval gate can probe the full context assembly
# without importing fastapi. This router only wires them.


async def _persist_turn(
    *,
    store: MemoryStore,
    user_id: str,
    persona_id: str,
    convo_id: str,
    user_msg: str,
    assistant_msg: str,
    usage: Usage,
    user_event_id: str,
    assistant_event_id: str,
    user_salience_score: SalienceScore | None = None,
    user_embedding: list[float] | None = None,
    assistant_embedding: list[float] | None = None,
    embedding_model: str | None = None,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
) -> None:
    """Append the user + assistant events and the usage row. Best-effort.

    ``user_embedding``/``assistant_embedding`` are optional semantic write-path
    vectors (Phase 3a), computed in the post-turn window while the BYOK key is
    legitimately alive; ``embedding_model`` records their space. ``None`` →
    the zero-config hash vector with a NULL model marker.

    ``user_event_id`` / ``assistant_event_id`` are pre-generated so the
    extraction pass can reference the new user event in ``source_event_ids``
    before the event is persisted. ``user_salience_score`` is the LLM-judged
    multi-dimensional score for the user's message (computed in the streaming
    block while the provider key is still alive). ``None`` → ``append_event``
    falls back to the heuristic — used for the assistant event and whenever no
    real provider served the turn.

    Family scope: ``family_id``/``visibility``/``participant_user_id`` are
    passed through to both events. The user event tags the speaker; the
    assistant event has ``participant_user_id=None`` (the assistant renders the
    same to every member). The ``usage`` row is constructed by the caller with
    ``family_id`` already set on it (see ``_stream``).
    """
    try:
        await append_event(
            store,
            user_id=user_id,
            persona_id=persona_id,
            convo_id=convo_id,
            role=EventRole.user,
            content=user_msg,
            event_id=user_event_id,
            salience_score=user_salience_score,
            embedding=user_embedding,
            embedding_model=embedding_model,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        await append_event(
            store,
            user_id=user_id,
            persona_id=persona_id,
            convo_id=convo_id,
            role=EventRole.assistant,
            content=assistant_msg,
            event_id=assistant_event_id,
            embedding=assistant_embedding,
            embedding_model=embedding_model,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=None,
        )
        await store.add_usage(usage)
    except Exception as exc:
        # Memory must never break a turn — the stream already succeeded. But
        # log the failure so silent "empty /memory" bugs (e.g. the events table
        # missing because alembic didn't apply) are diagnosable instead of
        # vanishing. The message carries no key material.
        logger.warning("memory persist failed (stream continues): %s: %s", type(exc).__name__, exc)


async def _apply_memory_ops(
    store: MemoryStore,
    ops: list[MemoryOp],
    *,
    user_id: str,
    persona_id: str,
    new_user_event_id: str,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
) -> None:
    """Apply extraction ops to the store. ``update``/``drop`` are validated
    against the persona's active memories in the same scope (LLM-hallucinated
    ids are skipped). Best-effort: never raises (memory must not break a turn).

    Family scope: the read uses the same family scope as the current turn, so
    a family turn never mutates another scope's memories. New memories inherit
    the turn's family scope."""
    if not ops:
        return
    try:
        # Own-only, same-scope: a receiver must not mutate a donor's memories
        # via a share, and a turn must not mutate memories from a different
        # family scope. Validation against this set silently drops any
        # update/drop op the LLM aims at an out-of-scope memory id.
        existing = await store.list_memories(
            user_id=user_id,
            persona_id=persona_id,
            include_donors=False,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
        existing_ids = {m.id for m in existing}
        now = datetime.now(UTC)
        for op in ops:
            if op.action == "add":
                src = op.source_event_ids or ([new_user_event_id] if new_user_event_id else [])
                await store.add_memory(
                    Memory(
                        id=uuid.uuid4().hex,
                        user_id=user_id,
                        persona_id=persona_id,
                        content=op.content or "",
                        tags=list(op.tags),
                        salience=op.salience,
                        source_event_ids=src,
                        status=MemoryStatus.active,
                        created_at=now,
                        updated_at=now,
                        family_id=family_id,
                        visibility=visibility,
                        participant_user_id=participant_user_id,
                    )
                )
            elif op.action == "update" and op.id in existing_ids:
                await store.update_memory(
                    memory_id=op.id,
                    user_id=user_id,
                    persona_id=persona_id,
                    content=op.content or "",
                    tags=list(op.tags),
                    salience=op.salience,
                    source_event_ids=list(op.source_event_ids or []),
                    family_id=family_id,
                )
            elif op.action == "drop" and op.id in existing_ids:
                await store.supersede_memory(
                    memory_id=op.id,
                    user_id=user_id,
                    persona_id=persona_id,
                    family_id=family_id,
                )
    except Exception as exc:
        logger.warning(
            "memory apply ops failed (stream continues): %s: %s", type(exc).__name__, exc
        )


async def _post_turn_work(
    served_cand: object | None,
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    convo_id: str,
    new_user_msg: str,
    new_user_event_id: str,
    family_id: str | None = None,
    visibility: str = "private",
    participant_user_id: str | None = None,
    consolidate: bool = True,
    family_members: dict[str, str] | None = None,
    utility_model: str | None = None,
) -> tuple[SalienceScore | None, list[MemoryOp] | None]:
    """Judge the user message's salience, then (if salient enough) extract
    atomic memories. Runs after ``done`` so the user sees completion first;
    for BYOK the caller keeps the key alive across this. Returns
    ``(judged, memory_ops)`` — either may be ``None`` (no real provider served,
    judge failed, below threshold, or extraction failed). Never raises.

    ``consolidate`` gates the Phase 2c episodic-consolidation pass (one extra
    LLM call when a convo has accumulated enough old, unsummarized events) —
    the caller turns it off for idempotent retries (``skip_persist``) so a
    retried turn can't mint a duplicate episode memory.

    Family scope: the ``recent_window`` query and the ``list_memories`` query
    are scoped by the same family tuple as the turn (server-side filter on
    the family/visibility/participant predicate), so a turn only sees its own
    scope's recall and only mutates its own scope's memories."""
    if served_cand is None or getattr(served_cand, "is_mock", True):
        return None, None
    try:
        # P2: the judge is a simple classification — it runs on the kind's
        # cheap sibling model (same key), not the user's flagship chat model.
        # Extraction/consolidation below keep the serving model: they write
        # user-visible memory content.
        judged = await judge_salience(
            served_cand.adapter, utility_model or served_cand.model, new_user_msg
        )
        # P0 #3: extraction is gated on EITHER emotional salience OR factual
        # novelty — a calm "by the way, I moved to Berlin" carries a durable
        # identity fact at near-zero emotional salience, and skipping it loses
        # the fact forever (consolidation captures narrative, not atoms).
        if judged is None or (
            max(judged.salience, judged.factual_novelty) < EXTRACT_SALIENCE_THRESHOLD
        ):
            return judged, None
        # Recent window = prior events in this convo (scoped to the same family
        # scope as the turn) + a synthetic event for the new user message (not
        # persisted yet) so the extractor sees what was just said and can put
        # new_user_event_id in source_event_ids.
        try:
            # recent_window is a per-convo filter, not a family-scope filter —
            # I9: defense-in-depth — even though a convo_id is pure by the
            # minting convention (PLAN §Family, "convo never mixes scopes"),
            # filter recent_window by the turn's family scope too so a reused
            # convo_id can't pull cross-scope events into the extractor.
            recent = await store.recent_window(
                user_id=user_id,
                persona_id=persona_id,
                convo_id=convo_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        except Exception:
            recent = []
        recent.append(
            Event(
                id=new_user_event_id,
                user_id=user_id,
                persona_id=persona_id,
                role=EventRole.user,
                content=new_user_msg,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        )
        try:
            # Include donor memories so the extractor can dedup against facts the
            # receiver already sees via a share — avoids creating a receiver-side
            # copy of a donor fact (live link, no duplicates). Donor rows are
            # read-only here; any update/drop op against them is rejected in
            # _apply_memory_ops (which validates own-only, same-scope).
            existing = await store.list_memories(
                user_id=user_id,
                persona_id=persona_id,
                include_donors=True,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
        except Exception:
            existing = []
        ops = await extract_memories(
            served_cand.adapter,
            served_cand.model,
            recent_events=recent,
            existing_memories=existing,
            new_user_event_id=new_user_event_id,
            participants=family_members,
        )
        # Phase 2c: episodic consolidation — compress the oldest unsummarized
        # stretch of this convo into an episode memory (self-thresholded; a
        # no-op on most turns). Runs here so the BYOK key is still alive.
        if consolidate:
            episode = await maybe_consolidate(
                served_cand.adapter,
                served_cand.model,
                store,
                user_id=user_id,
                persona_id=persona_id,
                convo_id=convo_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
                family_members=family_members,
            )
            # Phase 3b: the second tier — accumulated episodes compress into
            # an era (self-thresholded; a no-op until ≥ERA_MIN_EPISODES).
            await maybe_consolidate_eras(
                served_cand.adapter,
                served_cand.model,
                store,
                user_id=user_id,
                persona_id=persona_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
            # P1: the relationship note rides the consolidation cadence — one
            # extra LLM call per new episode (~every 20+ turns), never per
            # turn. Rebuilt from the distilled layer, not from itself alone.
            if episode is not None:
                await maybe_update_relationship_note(
                    served_cand.adapter,
                    served_cand.model,
                    store,
                    user_id=user_id,
                    persona_id=persona_id,
                    family_id=family_id,
                    visibility=visibility,
                    participant_user_id=participant_user_id,
                )
        return judged, ops
    except Exception as exc:
        logger.warning(
            "post-turn memory work failed (stream continues): %s: %s", type(exc).__name__, exc
        )
        return None, None


async def _monthly_spend(
    store: MemoryStore,
    *,
    user_id: str,
    family_id: str | None = None,
) -> float:
    """Best-effort current-month spend for the budget check.

    Family turns roll up against the family budget (per ``family_id``); personal
    turns roll up against the personal budget (per ``user_id``). The two scopes
    are disjoint at the row level (a personal turn has ``family_id IS NULL``,
    a family turn has ``family_id == F``).

    The family rollup is **family-wide**: it sums every usage row tagged
    ``family_id == F`` across ALL members, not just the requesting member —
    otherwise a family of N could each spend up to the monthly cap (N× the
    budget) before the hard-stop fired. ``list_usage_by_family`` returns those
    rows; the personal path uses the per-user ``list_usage`` filtered to
    ``family_id IS NULL``.
    """
    now = datetime.now(UTC)
    if family_id is not None:
        try:
            records = await store.list_usage_by_family(family_id=family_id)
        except Exception:
            return 0.0
        # All rows here have family_id == F by construction; sum in-month.
        return sum(
            r.usage.cost_usd
            for r in records
            if r.created_at.year == now.year and r.created_at.month == now.month
        )
    try:
        records = await store.list_usage(user_id=user_id)
    except Exception:
        return 0.0
    return sum(
        r.usage.cost_usd
        for r in records
        if r.created_at.year == now.year
        and r.created_at.month == now.month
        and r.usage.family_id is None
    )


def _validate_family_scope(
    *, body: LlmStreamRequest, user_id: str, principal: Principal | None
) -> tuple[str | None, str, str]:
    """Pre-flight family-scope validation — runs in the HTTP handler BEFORE
    the SSE stream opens so 4xx errors surface as a proper HTTP response.

    Returns ``(family_id, visibility, participant_user_id)`` after normalization.
    Raises ``HTTPException`` on any rule violation.
    """
    family_id = body.family_id
    visibility = body.visibility or "private"
    participant_user_id = body.participant_user_id or user_id

    # Cheaper, more-specific rules first so the client sees the right 400
    # (e.g. "mutually exclusive") instead of a misleading 404 ("family not
    # found") when the cross-family check would also fire.
    if visibility == "shared" and family_id is None:
        raise HTTPException(status_code=400, detail="visibility=shared requires family_id")
    if body.enc_key_blob is not None and body.family_enc_key_blob is not None:
        # Mutual exclusion: a turn is either personal or family, never both.
        # This pins the family budget to family turns and prevents a confusion
        # attack where a personal blob is served from a family turn's chain.
        raise HTTPException(
            status_code=400,
            detail="enc_key_blob and family_enc_key_blob are mutually exclusive",
        )
    if body.family_enc_key_blob is not None and family_id is None:
        # A family blob without a family scope would be silently ignored by
        # the chain builder (the turn would fall through to the env chain and
        # the personal budget) — a client bug that must fail loudly, not
        # degrade onto a different key and budget.
        raise HTTPException(
            status_code=400,
            detail="family_enc_key_blob requires family_id",
        )
    if body.participant_user_id is not None and body.participant_user_id != user_id:
        # A principal can only speak as themselves; forging another member's
        # voice on a family turn would let one member leak private disclosures
        # attributed to another.
        raise HTTPException(
            status_code=403,
            detail="participant_user_id must match the authenticated principal",
        )
    if family_id is not None and (principal is None or principal.family_id != family_id):
        # Cross-family access: 404 (not 403), per the project convention — do
        # not leak the existence of a family the caller does not belong to.
        raise HTTPException(status_code=404, detail="family not found")

    return family_id, visibility, participant_user_id


async def _stream(
    *,
    body: LlmStreamRequest,
    request: Request,
    user_id: str,
    principal: Principal | None,
    family_id: str | None,
    visibility: str,
    participant_user_id: str,
) -> AsyncIterator[dict]:
    settings = get_settings(request)
    ecdh = get_session_ecdh(request)
    store = get_store(request)

    # Family members lookup — best-effort; on failure we fall through to an
    # empty mapping (events render plain, the family therapist can still
    # answer from the shared layer; we don't want a misconfigured family
    # store to break every turn).
    family_members: dict[str, str] = {}
    if family_id is not None:
        try:
            family_store = getattr(request.app.state, "family_store", None)
            if family_store is not None:
                members = await family_store.list_members(family_id=family_id)
                family_members = {
                    m.user_id: (
                        f"{m.family_display_name} ({m.relation})"
                        if m.relation
                        else m.family_display_name
                    )
                    for m in members
                }
        except Exception as exc:
            logger.warning(
                "family_members lookup failed (stream continues): %s: %s",
                type(exc).__name__,
                exc,
            )

    yield _evt({"type": "session", "convo_id": body.convo_id, "persona_id": body.persona_id})

    # Pre-generate the event ids so the extraction pass (which runs before
    # persist) can reference the new user event in source_event_ids. Generated
    # early so the inbound crisis short-circuit below can also persist a
    # continuous event chain with stable ids.
    new_user_event_id = uuid.uuid4().hex
    new_assistant_event_id = uuid.uuid4().hex

    # I8: reserve the idempotency key (if the client sent ``request_id``) before
    # any work that persists. A retry hitting an already-in-flight or already-
    # done key skips ALL persistence (crisis persist, normal persist, memory
    # ops) so the event chain isn't duplicated. The stream still runs so the
    # retrying client gets a fresh reply.
    idem_key = (user_id, body.convo_id, body.request_id) if body.request_id else None
    skip_persist = False
    if idem_key is not None:
        if idem_key in _idem_done or idem_key in _idem_inflight:
            skip_persist = True
        else:
            _idem_inflight.add(idem_key)

    # --- K8: inbound crisis screen. Run BEFORE build_chain so explicit
    # self-harm / suicidal-intent language never reaches the provider. We
    # short-circuit the turn with a compassionate, localized crisis-resource
    # reply emitted as ordinary tokens (the wire contract is unchanged: the
    # resource rides as token events), persist the user + assistant events so
    # the thread is continuous and recallable, and stop. This is the
    # deterministic floor — an LLM-judge guardrail is a post-MVP upgrade. The
    # screen is high-precision / low-recall by design; it does not claim to
    # detect all risk (see safety/screen.py honest-limits). ---
    crisis = screen_user_message(body.message)
    crisis_msg = crisis.localized_message(body.message)
    if crisis.level == "crisis" and crisis_msg:
        # Emit the resource as the assistant's reply tokens, then a zero-cost
        # mock usage + done. Persist so the turn is part of the event chain.
        # The resource is localized to the language of the triggering message
        # (Cyrillic → Russian) — a user in crisis must not get a template in
        # a language they may not read.
        yield _evt({"type": "token", "text": crisis_msg})
        usage = Usage(
            id=uuid.uuid4().hex,
            user_id=user_id,
            family_id=family_id,
            provider_kind="mock",
            model="safety-screen",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
        )
        yield _evt(_usage_evt(usage, "mock", "safety-screen"))
        yield _evt({"type": "done"})
        if not skip_persist:
            try:
                await _persist_turn(
                    store=store,
                    user_id=user_id,
                    persona_id=body.persona_id,
                    convo_id=body.convo_id,
                    user_msg=body.message,
                    assistant_msg=crisis_msg,
                    usage=usage,
                    user_event_id=new_user_event_id,
                    assistant_event_id=new_assistant_event_id,
                    user_salience_score=None,
                    family_id=family_id,
                    visibility=visibility,
                    participant_user_id=participant_user_id,
                )
            except Exception:
                # Persistence is best-effort; never break the turn over it.
                logger.warning("safety-screen persist failed", exc_info=True)
            if idem_key is not None:
                _idem_mark_done(idem_key)
        return

    try:
        # Family turns use the family key + family key_handle + family base_url
        # (the family vault, not the personal vault). The family passphrase
        # never enters this endpoint.
        if family_id is not None:
            enc_key_blob = body.family_enc_key_blob
            key_handle = body.family_key_handle
        else:
            enc_key_blob = body.enc_key_blob
            key_handle = body.key_handle
        # ADDITIVE envelope fallback: when the new client sends no per-turn
        # blob (``enc_key_blob is None``), resolve the BYOK key from the
        # server-side envelope store (``providers.api_key_ciphertext`` /
        # ``family_providers.api_key_ciphertext``). The legacy per-turn blob
        # path stays the primary (back-comat with existing clients + tests).
        byok_decrypted: DecryptedKey | None = None
        if enc_key_blob is None:
            byok_decrypted = await _resolve_byok_from_envelope(
                request=request,
                store=store,
                user_id=user_id,
                family_id=family_id,
                key_handle=key_handle,
            )
        cands = build_chain(
            enc_key_blob=enc_key_blob,
            settings=settings,
            ecdh=ecdh,
            model=body.model,
            byok_decrypted=byok_decrypted,
        )
    except ProviderResolutionError as exc:
        yield _evt({"type": "error", "message": redact(str(exc))})
        yield _evt({"type": "done"})
        if idem_key is not None and not skip_persist:
            _idem_inflight.discard(idem_key)
        return

    # The BYOK candidate (if any) carries the decrypted key to zeroize after run.
    byok_dk = next((c.decrypted for c in cands if c.decrypted is not None), None)

    # BYOK semantic memory (Phase 1a+): when the user configured an embedding
    # model on their provider, recall embeds with the SAME per-request key as
    # the chat call (no new key surface; the key str is request-scoped — same
    # honest-zeroize disclosure as the chat call). Precedence in recall_chains:
    # this override → server env embedder → hash. Any failure → hash, silently.
    byok_embedder = None
    if byok_dk is not None and body.embeddings_model:
        byok_embedder = SemanticEmbedder(
            model=body.embeddings_model,
            api_key=byok_dk.api_key_str(),
            base_url=byok_dk.base_url,
        )

    # P0 latency: the monthly-spend rollup only gates the provider chain (it
    # is read after context assembly) — start it now so the usage-table scan
    # overlaps the memory reads instead of adding to time-to-first-token.
    # ``_monthly_spend`` never raises (store failures return 0.0).
    spend_task = asyncio.create_task(_monthly_spend(store, user_id=user_id, family_id=family_id))

    # --- Phase 3: build memory-aware context BEFORE writing this turn. ---
    # Phase 1c (adaptive assembly): fetch a wide recent window once, then let
    # the pure adaptive layer decide how much to keep (an emotionally loaded
    # stretch stays intact, chitchat shrinks), how many chains to recall (the
    # companion leans on memory during emotional moments), and whether to
    # inject a factual emotional-context note.
    salient_msgs: list[dict[str, str]] = []
    recent_msgs: list[dict[str, str]] = []
    emotional_note: dict[str, str] | None = None
    memories_msg: dict[str, str] | None = None
    session_bridge: dict[str, str] | None = None
    relationship_msg: dict[str, str] | None = None
    open_loops_msg: dict[str, str] | None = None
    recalled_event_ids: list[str] = []
    if body.memory_on:
        # The recent window comes first (P1) — it feeds the expanded retrieval
        # query, ``recall_k``, the rendered window and the emotional note.
        recent_wide: list[Event] = []
        recent: list[Event] = []
        try:
            recent_wide = await store.recent_window(
                user_id=user_id,
                persona_id=body.persona_id,
                convo_id=body.convo_id,
                limit=adaptive.MAX_WINDOW,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
            recent = adaptive.trim_recent_window(recent_wide)
        except Exception:
            recent_wide = []
            recent = []
        # The live window and the emotional note depend only on ``recent`` —
        # built here, OUTSIDE the recall path, so a recall failure degrades
        # recall alone and never drops the thread the user is actually in.
        recent_msgs = _events_to_window(recent, family_members or None)
        emotional_note = adaptive.emotional_context_note(recent)
        # P1 (query expansion): retrieve with the current message PLUS the
        # trailing user messages — a bare "да, наверное" carries no topic; the
        # preceding messages do. The embedding cache makes the extra text free.
        prior_user_texts = [
            e.content[:_QUERY_EXPANSION_CHARS]
            for e in recent
            if (e.role.value if hasattr(e.role, "value") else str(e.role)) == "user"
        ][-_QUERY_EXPANSION_MSGS:]
        retrieval_query = "\n".join([*prior_user_texts, body.message])

        # The three reads below are independent of each other, and each costs
        # a store round-trip plus (memories/chains) possibly an embedding API
        # call — all on the time-to-first-token path. Run them concurrently;
        # each slot keeps its own try/except so one failing degrades that slot
        # alone (same per-slot semantics as the previous serial code).

        async def _memories_slot() -> tuple[
            dict[str, str] | None, dict[str, str] | None, dict[str, str] | None
        ]:
            # Phase 2b: the distilled long-term layer — atomic memories (incl.
            # episode summaries) rendered as one factual line. P0 #1: the
            # slots are split between the highest-salience rows (stable
            # identity core) and the rows most relevant to *this* message
            # (semantic when an embedder is available, hash otherwise). P1:
            # the relationship note and open loops are partitioned OUT of the
            # facts line into their own slots.
            try:
                mems = await store.list_memories(
                    user_id=user_id,
                    persona_id=body.persona_id,
                    include_donors=True,
                    family_id=family_id,
                    visibility=visibility,
                    participant_user_id=participant_user_id,
                    include_superseded=True,
                )
                actives = [m for m in mems if m.status == MemoryStatus.active]
                # P2: era compression supersedes its episodes — the active
                # layer shows the era summary, but the episode DETAIL stays
                # reachable by relevance. Superseded episodes join the pool
                # AFTER the actives (list order feeds the stable slots —
                # those stay active-only).
                fact_pool = [
                    m for m in actives if NOTE_TAG not in m.tags and "open_loop" not in m.tags
                ] + [
                    m
                    for m in mems
                    if m.status == MemoryStatus.superseded and "episode" in m.tags
                ]
                mem_embedder = byok_embedder or make_semantic_embedder(settings)
                picked = None
                if mem_embedder is not None:
                    picked = await rank_memories_semantic(
                        mem_embedder, fact_pool, retrieval_query
                    )
                if picked is None:
                    picked = rank_memories(fact_pool, retrieval_query)
                return (
                    memories_to_message(picked),
                    relationship_message(actives),
                    open_loops_message(actives),
                )
            except Exception:
                return None, None, None

        async def _chains_slot() -> tuple[list[dict[str, str]], list[str]]:
            try:
                chains = await store.recall_chains(
                    user_id=user_id,
                    persona_id=body.persona_id,
                    query=retrieval_query,
                    k=adaptive.recall_k(body.message, recent),
                    family_id=family_id,
                    visibility=visibility,
                    participant_user_id=participant_user_id,
                    embedder=byok_embedder,
                )
                # Phase 2a: what actually surfaced into context gets
                # reinforced after the turn persists (counteracts time decay
                # for material that keeps coming up).
                return (
                    chains_to_messages(chains, family_members=family_members or None),
                    [e.id for ch in chains for e in ch.events],
                )
            except Exception:
                return [], []

        async def _bridge_slot() -> dict[str, str] | None:
            # P0 #4: a fresh convo (no events yet) gets a one-line bridge from
            # the previous conversation — the moment continuity matters most
            # is exactly when the window is empty and the query is "hi".
            # ``build_session_bridge`` never raises (best-effort inside).
            if recent_wide:
                return None
            return await build_session_bridge(
                store,
                user_id=user_id,
                persona_id=body.persona_id,
                convo_id=body.convo_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
                family_members=family_members or None,
            )

        (
            (memories_msg, relationship_msg, open_loops_msg),
            (salient_msgs, recalled_event_ids),
            session_bridge,
        ) = await asyncio.gather(_memories_slot(), _chains_slot(), _bridge_slot())

    # Server-side fill of the family therapist prompt. The owner can
    # customise the ``fam`` persona's system prompt on /family; the wire
    # never carries the body (clients may be stale after a join, and the
    # server is the single source of truth for shared family content). When
    # ``body.persona_id == "fam"`` and the principal is in a family, we
    # resolve the family store's saved prompt and pass it as the override —
    # ``build_persona_block`` already prefers an override to the builtin
    # (memory/persona_block.py), so this is the only place the plumbing
    # touches. A lookup failure (e.g. family store unavailable, disbanded
    # family) falls back to the static builtin — logged as a warning, never
    # raised, so a transient store error never breaks a turn.
    override_prompt: str | None = body.persona_prompt
    override_tone = body.persona_tone.model_dump() if body.persona_tone else None
    if body.persona_id == "fam" and family_id is not None and override_prompt is None:
        family_store = getattr(request.app.state, "family_store", None)
        if family_store is not None:
            try:
                tp = await family_store.get_therapist_prompt(family_id=family_id)
                if tp.body is not None:
                    override_prompt = tp.body
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "family therapist prompt lookup failed (builtin used): %s: %s",
                    type(exc).__name__,
                    exc,
                )

    messages = build_context(
        persona_id=body.persona_id,
        message=body.message,
        recent_window=recent_msgs,
        salient_chains=salient_msgs,
        persona_prompt=override_prompt,
        persona_tone=override_tone,
        emotional_note=emotional_note,
        salient_memories=memories_msg,
        session_bridge=session_bridge,
        relationship_note=relationship_msg,
        open_loops=open_loops_msg,
    )

    # --- Phase 4: budget hard-stop OR hosted out-of-credits → skip real
    # providers, serve mock. Self-hosted uses the monthly budget meter only;
    # hosted also gates on the Principal's credit balance (entitlement). ---
    # Family turns roll up spend against the family budget (per family_id);
    # personal turns roll up against the personal budget (per user_id). The
    # two scopes are disjoint at the row level (a personal turn has
    # family_id IS NULL, a family turn has family_id == F).
    run_cands = cands
    spent = await spend_task
    budget = compute_budget(spent_usd=spent, monthly_budget_usd=settings.monthly_budget_usd)
    first_real = next((c for c in cands if not c.is_mock), None)
    out_of_credits = (
        settings.deployment_mode == "hosted"
        and principal is not None
        and principal.credits_usd <= 0
    )
    # A paid subscriber is metered SOLELY by the credits gate — the global
    # monthly budget hard-stop is skipped for them so a Pro subscriber who
    # prepaid $25 isn't cut off by the operator's $20 cap while they still have
    # credit balance. Free / self-hosted users keep the budget hard-stop. See
    # ``routing/entitlement.py`` for the plan-string discriminator.
    subscriber = is_paid_subscriber(principal, settings)
    gate_reason = None
    keep_byok = False  # out-of-credits exempts BYOK (the user's own key costs the operator nothing)
    if budget.hard_stop and not subscriber:
        gate_reason = "budget hard-stop (monthly cap reached)"
    elif out_of_credits:
        gate_reason = "out of credits"
        # Credits are the user's prepaid balance for operator-provided providers.
        # BYOK is the user's own key — it consumes no operator credits, so the
        # credits gate must not cut it: keep BYOK (+ mock as the safety net) and
        # paywall only operator-provided env-fallback / Ollama nodes. A free
        # hosted user with their own key keeps working; only env-fallback is
        # gated. (The budget hard-stop above is unchanged — it is the operator's
        # global monthly cap, a separate concern.)
        keep_byok = True
    cut_to_mock = False
    if gate_reason is not None:
        run_cands = [c for c in cands if c.is_mock or (keep_byok and c.decrypted is not None)]
        # The turn is only forced to mock when no BYOK candidate survives the
        # gate. When BYOK survives it serves the turn (mock is just the safety
        # net), so no gate→mock fallback is emitted or recorded.
        cut_to_mock = not any(c.decrypted is not None for c in run_cands)
        if cut_to_mock and first_real is not None:
            record_fallback(user_id, f"{first_real.kind} → mock ({gate_reason})")

    assistant_text = ""
    served = None
    # Phase 3a: semantic write-path vectors for the two new events, computed in
    # the post-turn window (BYOK embedder preferred, env embedder otherwise).
    # None → the hash vector is stored with a NULL embedding_model marker.
    write_embedder = byok_embedder or make_semantic_embedder(settings)

    async def drive() -> AsyncIterator[tuple[str, object]]:
        nonlocal served
        # Emit the gate→mock fallback once, before the mock runs — only when a
        # real provider was actually cut to mock (no BYOK kept). When BYOK
        # survives the gate it serves the turn, so no gate fallback is emitted.
        if gate_reason is not None and cut_to_mock and first_real is not None:
            yield ("fallback", (first_real.kind, "mock", gate_reason))
        async for tag, val in run_with_fallback(run_cands, messages, user_id=user_id):
            if tag == "served":
                served = val
            else:
                yield (tag, val)

    async def _stream_tokens() -> AsyncIterator[dict]:
        nonlocal assistant_text
        async for tag, val in drive():
            if tag == "fallback":
                fk, tk, reason = val  # type: ignore[misc]
                # I1: a provider that failed mid-stream may have already emitted
                # partial tokens (accumulated into ``assistant_text``). Drop them
                # so the persisted reply is only the fallback provider's full
                # output, not the dead partial + the recovery. The client mirrors
                # this on the fallback event (clears its visible bubble), so what
                # the user sees and what is saved stay in sync.
                assistant_text = ""
                yield _evt({"type": "fallback", "from_kind": fk, "to_kind": tk, "reason": reason})
            else:
                text = val  # type: ignore[assignment]
                assistant_text += text
                yield _evt({"type": "token", "text": text})

    async def _maybe_append_output_safety() -> AsyncIterator[dict]:
        # K8: defense-in-depth output screen. The persona block already
        # *instructs* the model to direct to emergency services, but
        # instructions are not enforcement. If the streamed reply contains
        # crisis language without already surfacing a resource, append the
        # resource as one final token so it reaches the user and is persisted
        # into assistant_text. No-op when the reply is clean or already
        # includes a resource line.
        nonlocal assistant_text
        out = screen_assistant_text(assistant_text)
        # Localize the appended resource to the reply's own language — the
        # model answers in the user's language, so Cyrillic → Russian.
        out_msg = out.localized_message(assistant_text)
        if out.level == "crisis" and out_msg:
            extra = "\n\n" + out_msg
            assistant_text += extra
            yield _evt({"type": "token", "text": extra})

    async def _after_done(
        served_cand: object | None, u: object, usage_kind: str, usage_model: str
    ) -> None:
        """Everything after ``done``: post-turn LLM work (judge + extract +
        consolidation + relationship note), semantic write vectors, persist,
        memory ops, reinforcement, idempotency mark, hosted metering.

        Bundled into ONE coroutine so the caller can ``asyncio.shield`` it
        (P1): sse-starlette cancels the generator when the client disconnects,
        and a user closing the tab right after seeing the reply used to
        silently kill the turn's memory formation. Never raises; on failure
        the idempotency reservation is released so a retry can proceed."""
        try:
            judged, memory_ops = await _post_turn_work(
                served_cand,
                store,
                user_id=user_id,
                persona_id=body.persona_id,
                convo_id=body.convo_id,
                new_user_msg=body.message,
                new_user_event_id=new_user_event_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
                consolidate=not skip_persist,
                family_members=family_members or None,
                utility_model=(
                    utility_model_for(
                        getattr(served_cand, "kind", ""),
                        getattr(served_cand, "model", ""),
                        override=settings.utility_model or None,
                    )
                    if served_cand is not None
                    else None
                ),
            )
            # P2: meter the post-turn LLM work (judge/extract/consolidation/
            # note) into honest usage rows — the dashboard and the monthly
            # budget must see the FULL cost of a turn. Metered even on an
            # idempotent retry: the retry genuinely re-spent these tokens.
            utility_cost = 0.0
            drain = (
                getattr(served_cand.adapter, "drain_utility_usage", None)
                if served_cand is not None
                else None
            )
            if drain is not None:
                for uu in drain():
                    utility_cost += uu.cost_usd
                    try:
                        await store.add_usage(
                            Usage(
                                id=uuid.uuid4().hex,
                                user_id=user_id,
                                family_id=family_id,
                                provider_kind=uu.provider_kind,  # type: ignore[arg-type]
                                model=uu.model,
                                prompt_tokens=uu.prompt_tokens,
                                completion_tokens=uu.completion_tokens,
                                cost_usd=uu.cost_usd,
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "utility usage metering failed (turn continues): %s: %s",
                            type(exc).__name__,
                            exc,
                        )
            # Semantic write-path vectors — while the BYOK key blob is still
            # legitimately alive (the embedder holds its own request-scoped
            # key str; same honest-zeroize disclosure as the chat call).
            user_vec: list[float] | None = None
            assistant_vec: list[float] | None = None
            write_model: str | None = None
            if write_embedder is not None and not skip_persist:
                vecs = await write_embedder.embed_batch([body.message, assistant_text])
                if vecs is not None:
                    user_vec, assistant_vec = vecs[0], vecs[1]
                    write_model = write_embedder.model
            # I8: a duplicate request_id (already in-flight or done) skips ALL
            # side effects — the first attempt already persisted everything.
            if skip_persist:
                return
            usage = Usage(
                id=uuid.uuid4().hex,
                user_id=user_id,
                family_id=family_id,
                provider_kind=usage_kind,  # type: ignore[arg-type]
                model=usage_model,
                prompt_tokens=u.prompt_tokens,  # type: ignore[attr-defined]
                completion_tokens=u.completion_tokens,  # type: ignore[attr-defined]
                cost_usd=u.cost_usd,  # type: ignore[attr-defined]
            )
            await _persist_turn(
                store=store,
                user_id=user_id,
                persona_id=body.persona_id,
                convo_id=body.convo_id,
                user_msg=body.message,
                assistant_msg=assistant_text,
                usage=usage,
                user_event_id=new_user_event_id,
                assistant_event_id=new_assistant_event_id,
                user_salience_score=judged,
                user_embedding=user_vec,
                assistant_embedding=assistant_vec,
                embedding_model=write_model,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
            await _apply_memory_ops(
                store,
                memory_ops or [],
                user_id=user_id,
                persona_id=body.persona_id,
                new_user_event_id=new_user_event_id,
                family_id=family_id,
                visibility=visibility,
                participant_user_id=participant_user_id,
            )
            # Phase 2a: reinforce what was recalled into this turn's context.
            # Gated on the same idempotency as persist (a retried turn must
            # not double-bump). Best-effort.
            if recalled_event_ids:
                try:
                    await store.reinforce_events(
                        user_id=user_id, event_ids=recalled_event_ids
                    )
                except Exception as exc:
                    logger.warning(
                        "recall reinforcement failed (stream continues): %s: %s",
                        type(exc).__name__,
                        exc,
                    )
            if idem_key is not None:
                _idem_mark_done(idem_key)
            # Hosted metering: best-effort credit debit for this turn's cost —
            # chat stream PLUS the post-turn utility calls (P2, honest total).
            # Self-hosted never touches credits. The debit is atomic
            # (conditional UPDATE); a False return means the balance didn't
            # cover the cost — logged, turn NOT reversed (already delivered).
            total_cost = u.cost_usd + utility_cost  # type: ignore[attr-defined]
            if settings.deployment_mode == "hosted" and principal is not None and total_cost > 0:
                try:
                    auth_store = request.app.state.auth_store
                    debited = await auth_store.decrement_credits(
                        user_id=principal.user_id, amount=total_cost
                    )
                    if not debited:
                        logger.warning(
                            "insufficient credits for user %s (cost %.6f) — turn not reversed",
                            principal.user_id,
                            total_cost,
                        )
                except Exception:  # noqa: BLE001 — metering is best-effort
                    logger.warning("credit decrement failed for user %s", principal.user_id)
        except Exception as exc:
            # done already went out — log and swallow so the wire contract
            # stays intact; release the reservation so a retry can proceed.
            logger.warning(
                "post-done work failed (stream continues): %s: %s", type(exc).__name__, exc
            )
            if idem_key is not None and not skip_persist:
                _idem_inflight.discard(idem_key)

    async def _run_after_done_shielded(
        served_cand: object | None, u: object, usage_kind: str, usage_model: str
    ) -> None:
        """Run ``_after_done`` shielded from client-disconnect cancellation.

        On disconnect the outer await is cancelled (and re-raised so the
        generator unwinds normally), but the inner task keeps running on the
        loop to completion — the turn's memory formation no longer depends on
        the user keeping the tab open. The BYOK zeroize window may close
        before the background task finishes; that wipes the source bytearray
        while the adapter/embedder keep their request-scoped key ``str``
        (exactly the honest-zeroize disclosure in CLAUDE.md)."""
        task = asyncio.create_task(_after_done(served_cand, u, usage_kind, usage_model))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            logger.info(
                "client disconnected after done — turn memory work continues in background"
            )
            raise

    # I2: track whether ``done`` has been emitted so the except path never emits
    # a second ``done`` (or an error-after-done). Post-turn work and persist run
    # AFTER ``done``; if anything there throws, the guard keeps the wire contract
    # ``session → token → usage → done`` intact instead of appending error+done.
    done_sent = False
    try:
        if byok_dk is not None:
            with zeroized(byok_dk.api_key):
                async for evt in _stream_tokens():
                    yield evt
                async for evt in _maybe_append_output_safety():
                    yield evt
                # Usage + done first — the user sees completion immediately.
                served_cand, u, usage_kind, usage_model = _resolve_served(served, cands)
                yield _evt(_usage_evt(u, usage_kind, usage_model))
                yield _evt({"type": "done"})
                done_sent = True
                # Post-turn LLM work runs while the BYOK key is still alive:
                # judge + (if salient) extract + consolidation, then persist.
                await _run_after_done_shielded(served_cand, u, usage_kind, usage_model)
        else:
            async for evt in _stream_tokens():
                yield evt
            async for evt in _maybe_append_output_safety():
                yield evt
            served_cand, u, usage_kind, usage_model = _resolve_served(served, cands)
            yield _evt(_usage_evt(u, usage_kind, usage_model))
            yield _evt({"type": "done"})
            done_sent = True
            # Env-key path: the adapter holds the env key (not zeroized), so
            # the post-done work needs no zeroize window.
            await _run_after_done_shielded(served_cand, u, usage_kind, usage_model)
    except Exception as exc:
        if not done_sent:
            yield _evt(
                {"type": "error", "message": redact(f"stream interrupted: {type(exc).__name__}")}
            )
            yield _evt({"type": "done"})
        else:
            # done already sent — log and swallow so we never emit a second
            # done or an error-after-done (memory/persist are best-effort).
            logger.warning(
                "post-done work failed (done already sent): %s: %s",
                type(exc).__name__,
                exc,
            )
        # I8: a failed turn never reached persist — release the reservation so
        # a retry with the same request_id can proceed (instead of being
        # permanently blocked as "in-flight").
        if idem_key is not None and not skip_persist:
            _idem_inflight.discard(idem_key)
        return


@router.post("/llm/stream")
# I15: per-user cost cap (the cost-critical axis in hosted multi-user) plus a
# per-IP burst cap. Stacked: both must pass. Per-user keys off the authenticated
# Principal (set on request.state by AuthMiddleware before this runs).
@limiter.limit("120/minute", key_func=get_remote_address)
@limiter.limit("30/minute", key_func=user_or_ip_key)
async def llm_stream(
    body: LlmStreamRequest,
    request: Request,
    user_id: UserIdDep,
    principal: PrincipalDep,
) -> EventSourceResponse:
    # Pre-flight family-scope validation runs in the HTTP handler so 4xx errors
    # surface as a proper HTTP response — once ``EventSourceResponse`` has sent
    # the 200 + ``text/event-stream`` headers, an HTTPException can't be turned
    # into a different response and the SSE stream is mid-flight already.
    family_id, visibility, participant_user_id = _validate_family_scope(
        body=body, user_id=user_id, principal=principal
    )
    return EventSourceResponse(
        _stream(
            body=body,
            request=request,
            user_id=user_id,
            principal=principal,
            family_id=family_id,
            visibility=visibility,
            participant_user_id=participant_user_id,
        )
    )
