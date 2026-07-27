"""Cross-persona live memory shares (``/v1/memory/shares`` + store union logic).

A share is a donor→receiver reference, not a copy. The receiver's read paths
(``list_memories``, ``recall_candidates``) union the donor's rows while the link
exists; the receiver's mutation validation (``list_memories(include_donors=False)``)
stays own-only so a receiver cannot update/drop a donor memory. Removing the
link detaches the donor's rows from the receiver without deleting anything.

The HTTP tests cover the endpoint CRUD + self-share rejection against the app's
in-memory store (the default in tests). The store tests assert the union and
own-only invariants directly against ``InMemoryStore``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ai_companion_contracts import Event, EventRole, Memory, MemoryStatus

from ai_companion_api.memory.store import InMemoryStore

# --- HTTP: /v1/memory/shares CRUD -------------------------------------------


async def test_add_and_list_share(client) -> None:
    r = await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "aria", "receiver_persona_id": "sam"},
    )
    assert r.status_code == 200
    share = r.json()
    assert share["donor_persona_id"] == "aria"
    assert share["receiver_persona_id"] == "sam"
    assert "id" in share and "created_at" in share

    r = await client.get("/v1/memory/shares", params={"donor_persona_id": "aria"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["receiver_persona_id"] == "sam"


async def test_self_share_rejected(client) -> None:
    r = await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "aria", "receiver_persona_id": "aria"},
    )
    assert r.status_code == 400


async def test_add_share_idempotent(client) -> None:
    body = {"donor_persona_id": "aria", "receiver_persona_id": "sam"}
    first = await client.post("/v1/memory/shares", json=body)
    assert first.status_code == 200
    second = await client.post("/v1/memory/shares", json=body)
    assert second.status_code == 200
    # Same triple → same id (no duplicate link).
    assert second.json()["id"] == first.json()["id"]

    rows = (await client.get("/v1/memory/shares", params={"donor_persona_id": "aria"})).json()
    assert len(rows) == 1


async def test_remove_share(client) -> None:
    await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "aria", "receiver_persona_id": "sam"},
    )
    r = await client.delete(
        "/v1/memory/shares",
        params={"donor_persona_id": "aria", "receiver_persona_id": "sam"},
    )
    assert r.status_code == 204
    rows = (await client.get("/v1/memory/shares", params={"donor_persona_id": "aria"})).json()
    assert rows == []


# --- Store: union + own-only invariants -------------------------------------


def _memory(pid: str, content: str, *, mid: str = "m1") -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid,
        user_id="u",
        persona_id=pid,
        content=content,
        tags=["work"],
        salience=0.8,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=now,
        updated_at=now,
    )


def _event(pid: str, content: str, *, eid: str = "e1") -> Event:
    return Event(
        id=eid,
        user_id="u",
        persona_id=pid,
        role=EventRole.user,
        content=content,
        salience=0.5,
    )


@pytest.mark.asyncio
async def test_list_memories_unions_donor() -> None:
    store = InMemoryStore()
    await store.add_memory(_memory("aria", "User has a dog named Maple", mid="aria-m1"))

    await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="sam")

    # Default (include_donors=True): receiver sees the donor's active memory,
    # still carrying the donor's persona_id so the UI can attribute it.
    rows = await store.list_memories(user_id="u", persona_id="sam")
    assert len(rows) == 1
    assert rows[0].persona_id == "aria"
    assert "Maple" in rows[0].content

    # Own-only: the receiver has no memories of its own — donor rows excluded.
    own = await store.list_memories(user_id="u", persona_id="sam", include_donors=False)
    assert own == []


@pytest.mark.asyncio
async def test_recall_unions_donor_events() -> None:
    store = InMemoryStore()
    await store.add_event(_event("aria", "My dog Maple died.", eid="aria-e1"))

    await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="sam")

    cands = await store.recall_candidates(user_id="u", persona_id="sam")
    assert any(c.persona_id == "aria" and "Maple" in c.content for c in cands)

    chains = await store.recall_chains(user_id="u", persona_id="sam", query="dog name")
    assert chains, "expected a recalled chain spanning the donor's event"
    blob = " ".join(e.content for ch in chains for e in ch.events)
    assert "Maple" in blob

    # Family-scope predicate (PLAN §16 #1, #6): donor rows are personal-
    # scoped and MUST NOT surface in a family recall, even when the share
    # is active. This is the cross-donor / family-isolation invariant.
    # InMemoryStore.recall_chains doesn't take family kwargs; we exercise
    # the predicate via recall_candidates directly, which is what
    # ``recall_chains`` would call under the hood for the Postgres impl.
    fam_cands = await store.recall_candidates(
        user_id="u",
        persona_id="sam",
        family_id="fam-x",
        visibility="private",
        participant_user_id="u",
    )
    assert not any(c.id == "aria-e1" for c in fam_cands)


@pytest.mark.asyncio
async def test_list_donors_direction() -> None:
    store = InMemoryStore()
    await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="sam")
    await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="nico")

    # Donor-side view: aria shares with sam + nico.
    shares = await store.list_shares(user_id="u", donor_persona_id="aria")
    assert {s.receiver_persona_id for s in shares} == {"sam", "nico"}

    # Receiver-side view: sam's donors are [aria] only.
    donors = await store.list_donors(user_id="u", receiver_persona_id="sam")
    assert donors == ["aria"]


@pytest.mark.asyncio
async def test_remove_share_detaches_donor_rows() -> None:
    store = InMemoryStore()
    await store.add_memory(_memory("aria", "User has a dog named Maple", mid="aria-m1"))
    await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="sam")

    assert len(await store.list_memories(user_id="u", persona_id="sam")) == 1

    await store.remove_share(user_id="u", donor_persona_id="aria", receiver_persona_id="sam")

    # Link revoked → donor memory vanishes from the receiver's view…
    assert await store.list_memories(user_id="u", persona_id="sam") == []
    # …but stays with the donor (nothing was copied or deleted).
    own = await store.list_memories(user_id="u", persona_id="aria")
    assert len(own) == 1
    assert own[0].id == "aria-m1"


@pytest.mark.asyncio
async def test_self_share_raises() -> None:
    store = InMemoryStore()
    with pytest.raises(ValueError):
        await store.add_share(user_id="u", donor_persona_id="aria", receiver_persona_id="aria")
