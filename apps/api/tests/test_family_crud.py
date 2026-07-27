"""Family CRUD HTTP: create / rename / disband / leave / remove.

End-to-end through the FastAPI router with the real local auth flow
(``make_app`` + ``app_client``). Each test signs up two distinct users
(separate ``ac`` contexts with isolated cookies) so the family-scope
invariants get a real verified Principal — the dev insecure-header escape
hatch does NOT load user rows, so it can never represent "user is in a
family" or "is the owner".
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from ai_companion_contracts import FamilyRole


@pytest.fixture
async def ac(make_app, app_client):
    """Per-test app + AsyncClient with cookies. Tests can ``await _signup(ac, ...)``."""

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as c:
            yield c

    async with _ctx() as c:
        yield c


async def _signup(ac, email: str, password: str = "pwaaaaaaaaaa") -> str:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code in (200, 201), r.text
    me = await ac.get("/v1/auth/me")
    assert me.status_code == 200
    return me.json()["user_id"]


async def _second_app(make_app, app_client):
    """Return an ``async with``-compatible factory of a fresh app+client."""

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as c:
            yield c

    return _ctx()


async def test_create_then_get_returns_owner(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        r = await ac.post("/v1/family", json={"name": "Cohort A"})
        assert r.status_code == 200, r.text
        assert r.json()["owner_user_id"]
        r2 = await ac.get("/v1/family")
        assert r2.status_code == 200
        body = r2.json()
        assert body["family"]["name"] == "Cohort A"
        assert any(m["family_role"] == "owner" for m in body["members"])


async def test_create_twice_returns_409(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        r1 = await ac.post("/v1/family", json={"name": "Cohort A"})
        assert r1.status_code == 200
        r2 = await ac.post("/v1/family", json={"name": "Cohort B"})
        assert r2.status_code == 409


async def test_get_when_not_in_family_returns_404(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "lonely@x.com")
        r = await ac.get("/v1/family")
        assert r.status_code == 404


async def test_rename_owner_only(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await ac.post("/v1/family", json={"name": "Original"})
        r = await ac.patch("/v1/family", json={"name": "Renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed"
    # A second user with no family cannot rename anything.
    async with await _second_app(make_app, app_client) as ac2:
        await _signup(ac2, "stranger@x.com")
        r2 = await ac2.patch("/v1/family", json={"name": "X"})
        assert r2.status_code == 404


async def test_disband_owner_only_and_wipes_data(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await ac.post("/v1/family", json={"name": "Cohort"})
        r = await ac.delete("/v1/family")
        assert r.status_code == 204
        r2 = await ac.get("/v1/family")
        assert r2.status_code == 404
        # Second disband is 404 (idempotent on missing target).
        r3 = await ac.delete("/v1/family")
        assert r3.status_code == 404


async def test_disband_clears_users_family_id_for_owner(make_app, app_client) -> None:
    """Regression for the "users.family_id stays set after disband" bug.

    The previous order was: ``wipe_family_scope`` (drops
    ``family_members`` rows) → ``list_members`` (returns empty) → the
    loop never fires → ``users.family_id`` is never cleared. A user
    with a stale ``users.family_id`` could then re-create a family
    (the 409 guard passed because the field was still set) — or even
    accept an invite — and end up with multiple ``family_members``
    rows, which is exactly the data state that triggered the
    ``/v1/llm/stream`` 404 for family turns.

    After disband, ``GET /v1/auth/me`` MUST report
    ``family_id == None`` — the principal pointer is cleared.
    """
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await ac.post("/v1/family", json={"name": "Cohort"})
        # Pre-disband: principal is attached to a family.
        me = await ac.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["family_id"] is not None
        # Disband.
        r = await ac.delete("/v1/family")
        assert r.status_code == 204
        # Post-disband: the principal pointer is cleared. This is the
        # invariant the create_family 409 guard relies on.
        me2 = await ac.get("/v1/auth/me")
        assert me2.status_code == 200
        assert me2.json()["family_id"] is None
        assert me2.json()["family_role"] is None


async def test_disband_clears_users_family_id_for_member(make_app, app_client) -> None:
    """The same invariant holds for non-owner members. The owner
    disbands; the member's ``users.family_id`` is also cleared.

    Both clients share a single app (and therefore a single in-memory
    family store) so the member's ``add_member`` /
    ``set_user_family`` calls land in the same store the owner's
    disband will sweep.
    """
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from ai_companion_api.main import lifespan

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with lifespan(app):
            transport_a = ASGITransport(app=app)
            transport_b = ASGITransport(app=app)
            async with AsyncClient(transport=transport_a, base_url="http://test") as ac_owner:
                async with AsyncClient(transport=transport_b, base_url="http://test") as ac_member:
                    yield ac_owner, ac_member, app

    async with _ctx() as (ac_owner, ac_member, app):
        await _signup(ac_owner, "owner@x.com")
        fam = (await ac_owner.post("/v1/family", json={"name": "Cohort"})).json()

        # Sign up the member on the same app so the family store is
        # shared. The bug is in disband, not accept — we drive the
        # store directly to skip the invite/email transport roundtrip.
        member_id = await _signup(ac_member, "member@x.com")
        await app.state.family_store.add_member(
            family_id=fam["id"],
            user_id=member_id,
            family_role=FamilyRole.member,
            family_display_name="Member",
            relation="other",
            color="#7c3aed",
        )
        await app.state.auth_store.set_user_family(
            user_id=member_id,
            family_id=fam["id"],
            family_role=FamilyRole.member.value,
        )
        # Sanity: member is attached.
        me = await ac_member.get("/v1/auth/me")
        assert me.json()["family_id"] == fam["id"]
        # Owner disbands.
        r = await ac_owner.delete("/v1/family")
        assert r.status_code == 204
        # The member's pointer is also cleared.
        me2 = await ac_member.get("/v1/auth/me")
        assert me2.status_code == 200
        assert me2.json()["family_id"] is None


async def test_owner_cannot_self_leave(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await ac.post("/v1/family", json={"name": "Cohort"})
        r = await ac.delete("/v1/family/members/me")
        assert r.status_code == 403
        detail = r.json()["detail"].lower()
        assert "disband" in detail or "transfer" in detail


async def test_non_member_cannot_remove_member(make_app, app_client) -> None:
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await ac.post("/v1/family", json={"name": "Cohort"})
    async with await _second_app(make_app, app_client) as ac2:
        await _signup(ac2, "stranger@x.com")
        r = await ac2.delete("/v1/family/members/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404


async def test_add_member_is_idempotent(make_app, app_client) -> None:
    """Regression for the 409 on ``POST /v1/family`` in Postgres mode.

    ``routers/family.py:create_family`` first calls ``create_family`` (which
    materializes the owner member row) and then ``add_member`` to set the
    display name. A naive INSERT in the second call collides on the
    composite PK and 500s; the Postgres impl is an upsert
    (``ON CONFLICT ... DO UPDATE``) so the second call just refreshes the
    row. The in-memory impl has always been idempotent. This test exercises
    the second-call path through the real router on the default in-memory
    store — if the router starts calling something stricter on Postgres, the
    fixtures will catch it.
    """
    # Direct store-level: calling add_member twice for the same
    # (family_id, user_id) MUST NOT raise.
    from ai_companion_contracts import FamilyRole

    from ai_companion_api.family.store import InMemoryFamilyStore

    store = InMemoryFamilyStore()
    fam = await store.create_family(name="Cohort", owner_user_id="u-owner")
    m1 = await store.add_member(
        family_id=fam.id,
        user_id="u-owner",
        family_role=FamilyRole.owner,
        family_display_name="Alex",
        relation="parent",
        color="#7c3aed",
    )
    m2 = await store.add_member(
        family_id=fam.id,
        user_id="u-owner",
        family_role=FamilyRole.owner,
        family_display_name="Alex",
        relation="parent",
        color="#7c3aed",
    )
    assert m1.user_id == m2.user_id == "u-owner"
    members = await store.list_members(family_id=fam.id)
    assert len(members) == 1, "second add_member MUST NOT duplicate the row"

    # And via the HTTP layer: the create path runs the same sequence on
    # Postgres and must not 500 with "family member already exists".
    async with await _second_app(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        r = await ac.post("/v1/family", json={"name": "Cohort"})
        assert r.status_code == 200, r.text
        # Subsequent GET works (members list includes the owner).
        r2 = await ac.get("/v1/family")
        assert r2.status_code == 200
        members = r2.json()["members"]
        assert len(members) == 1
        assert members[0]["family_role"] == "owner"
