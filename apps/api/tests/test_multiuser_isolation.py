"""Sprint 6 M1 — horizontal user isolation across the API surface.

Every per-user store query is scoped by ``user_id``; cross-user reads/writes
return 404 (not 403) per the project's cross-tenant convention. M1.1 adds
defense-in-depth at the STORE layer (``update_memory``/``supersede_memory``
filter by user_id/persona_id/family_id) so even a caller that bypassed the
router's own-ids check could not mutate another user's memory. M1.3 enforces
the family-scope invariant on memory/journal reads+writes. M1.2 removed the
implicit ``default_user_id`` fallback so a missing identity 401s.

The HTTP tests use the ``client`` fixture (escape hatch ON) with explicit
``X-User-Id`` headers to impersonate distinct users. The store-level test
exercises ``InMemoryStore`` directly (no app needed).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from ai_companion_contracts import Memory, MemoryStatus

from ai_companion_api.memory.store import InMemoryStore

U1 = "u1-isolation"
U2 = "u2-isolation"


# --- M1.1: store-layer defense-in-depth ------------------------------------


async def test_update_memory_rejects_cross_user() -> None:
    """M1.1: ``update_memory`` filters by user_id — a call with u2's user_id
    must NOT mutate u1's memory (no-op), even with the correct memory id."""
    store = InMemoryStore()
    now = datetime.now(UTC)
    mem = Memory(
        id="m1",
        user_id=U1,
        persona_id="sam",
        content="u1's secret",
        tags=["x"],
        salience=0.5,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=now,
        updated_at=now,
    )
    store._memories.append(mem)

    # u2 attempts to rewrite u1's memory by id.
    await store.update_memory(
        user_id=U2,
        persona_id="sam",
        memory_id="m1",
        content="hijacked",
        tags=["hacked"],
        salience=0.99,
        source_event_ids=[],
    )
    fresh = next(m for m in store._memories if m.id == "m1")
    assert fresh.content == "u1's secret"
    assert fresh.tags == ["x"]
    assert fresh.salience == 0.5


async def test_supersede_memory_rejects_cross_user() -> None:
    """M1.1: ``supersede_memory`` with u2's user_id does not supersede u1's
    memory — u1's row stays ``active`` and ``superseded_by`` stays None."""
    store = InMemoryStore()
    now = datetime.now(UTC)
    store._memories.append(
        Memory(
            id="m1",
            user_id=U1,
            persona_id="sam",
            content="u1's fact",
            tags=[],
            salience=0.4,
            source_event_ids=[],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
        )
    )
    await store.supersede_memory(user_id=U2, persona_id="sam", memory_id="m1", superseded_by="m2")
    fresh = next(m for m in store._memories if m.id == "m1")
    # Still active — the cross-user call was a no-op. (The contract ``Memory``
    # does not expose ``superseded_by``; status is the observable signal.)
    assert fresh.status == MemoryStatus.active


async def test_update_memory_scoped_by_persona_and_family() -> None:
    """M1.1: even with the right user_id, a wrong persona_id or family_id is a
    no-op — the store layer's own filter, independent of the router's
    existing-ids check."""
    store = InMemoryStore()
    now = datetime.now(UTC)
    store._memories.append(
        Memory(
            id="m1",
            user_id=U1,
            persona_id="sam",
            content="orig",
            tags=[],
            salience=0.4,
            source_event_ids=[],
            status=MemoryStatus.active,
            created_at=now,
            updated_at=now,
            family_id="fam-1",
        )
    )
    # Correct user + memory id, but WRONG persona → no-op.
    await store.update_memory(
        user_id=U1,
        persona_id="aria",
        memory_id="m1",
        content="x",
        tags=[],
        salience=0.1,
        source_event_ids=[],
    )
    assert next(m for m in store._memories if m.id == "m1").content == "orig"
    # Correct user + persona, but WRONG family → no-op.
    await store.update_memory(
        user_id=U1,
        persona_id="sam",
        family_id="fam-other",
        memory_id="m1",
        content="x",
        tags=[],
        salience=0.1,
        source_event_ids=[],
    )
    assert next(m for m in store._memories if m.id == "m1").content == "orig"
    # Correct user + persona + family → mutates.
    await store.update_memory(
        user_id=U1,
        persona_id="sam",
        family_id="fam-1",
        memory_id="m1",
        content="updated",
        tags=[],
        salience=0.1,
        source_event_ids=[],
    )
    assert next(m for m in store._memories if m.id == "m1").content == "updated"


# --- M1.3: HTTP-level cross-user isolation ---------------------------------


async def test_journal_cross_user_is_404(client) -> None:
    # u1 authors a journal entry; u2 cannot PATCH or DELETE it (404, not 403
    # and not 200). u1 still can.
    r = await client.post(
        "/v1/journal",
        json={"persona_id": "lou", "body": "u1 private diary", "tags": []},
        headers={"X-User-Id": U1},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["id"]

    patch = await client.patch(
        f"/v1/journal/{eid}",
        json={"body": "hijacked"},
        headers={"X-User-Id": U2},
    )
    assert patch.status_code == 404

    delete = await client.delete(f"/v1/journal/{eid}", headers={"X-User-Id": U2})
    assert delete.status_code == 404

    # u2 can't read it via list either (scoped by user_id).
    u2_list = await client.get("/v1/journal", headers={"X-User-Id": U2})
    assert u2_list.status_code == 200
    assert all(e["id"] != eid for e in u2_list.json())

    # u1 can delete their own.
    own_del = await client.delete(f"/v1/journal/{eid}", headers={"X-User-Id": U1})
    assert own_del.status_code == 204


async def test_memory_shares_cross_user_not_visible(client) -> None:
    # u1 creates a donor share; u2 listing shares for the same donor persona
    # sees only their own (empty), never u1's link.
    r = await client.post(
        "/v1/memory/shares",
        json={"donor_persona_id": "sam", "receiver_persona_id": "aria"},
        headers={"X-User-Id": U1},
    )
    assert r.status_code == 200, r.text
    u2_list = await client.get("/v1/memory/shares?donor_persona_id=sam", headers={"X-User-Id": U2})
    assert u2_list.status_code == 200
    assert u2_list.json() == []
    u1_list = await client.get("/v1/memory/shares?donor_persona_id=sam", headers={"X-User-Id": U1})
    assert len(u1_list.json()) == 1


async def test_cross_family_scope_is_404(client) -> None:
    """M1.3: the default Principal has no family. Naming any ``family_id`` on
    the family-scoped memory/journal endpoints 404s — the caller is not in
    that family. Mirrors /llm/stream's existing guard."""
    # GET /v1/memory?family_id=…
    r = await client.get(
        "/v1/memory?persona_id=sam&family_id=fam-not-mine", headers={"X-User-Id": U1}
    )
    assert r.status_code == 404
    # POST /v1/memory/recall with family_id
    r = await client.post(
        "/v1/memory/recall",
        json={"persona_id": "sam", "query": "anything", "family_id": "fam-not-mine"},
        headers={"X-User-Id": U1},
    )
    assert r.status_code == 404
    # GET /v1/memories?family_id=…
    r = await client.get(
        "/v1/memories?persona_id=sam&family_id=fam-not-mine", headers={"X-User-Id": U1}
    )
    assert r.status_code == 404
    # GET /v1/conversations?family_id=…
    r = await client.get("/v1/conversations?family_id=fam-not-mine", headers={"X-User-Id": U1})
    assert r.status_code == 404
    # GET /v1/journal?family_id=…
    r = await client.get("/v1/journal?family_id=fam-not-mine", headers={"X-User-Id": U1})
    assert r.status_code == 404


# --- M1.2: no implicit default_user_id; header ignored without escape hatch -


async def test_missing_identity_401_without_escape_hatch(make_app, app_client) -> None:
    """M1.2: with the escape hatch OFF, a request with no cookie AND no
    ``X-User-Id`` header 401s — it does NOT impersonate ``default_user_id``."""
    async with _client(make_app, app_client) as ac:
        r = await ac.get("/v1/journal")
        assert r.status_code == 401


async def test_x_user_id_ignored_without_escape_hatch(make_app, app_client) -> None:
    """M1.2: with the escape hatch OFF, an ``X-User-Id`` header is NOT honored
    — the Principal comes only from the session cookie. A header-only request
    (no cookie) 401s even though the header is present."""
    async with _client(make_app, app_client) as ac:
        r = await ac.get("/v1/journal", headers={"X-User-Id": "anyone"})
        assert r.status_code == 401


def _client(make_app, app_client):
    @asynccontextmanager
    async def _ctx():
        app = make_app()  # escape hatch OFF by default
        async with app_client(app) as c:
            yield c

    return _ctx()
