"""Regression for the "stale family_id" bug that caused 404 on
``/v1/llm/stream`` for family turns.

The bug: ``FamilyStore.get_family_for_user`` did a
``SELECT ... LIMIT 1`` with no ``ORDER BY`` over ``family_members``,
so when a user had multiple ``family_members`` rows (e.g. older
memberships that were never cleaned up after disband), it returned an
arbitrary family that did not match ``users.family_id`` (the
principal's ``family_id``). The LLM stream endpoint compares
``body.family_id`` against ``principal.family_id`` and 404s on a
mismatch.

The fix has two parts (pinned here):

1. ``get_family_for_user`` now accepts a ``preferred_family_id``
   parameter. When the user is a member of that family, it is
   returned — guaranteeing the result matches ``users.family_id``.

2. As a defense in depth, when ``preferred_family_id`` is ``None``
   or the user is not a member of it, the result is ordered by
   ``joined_at DESC`` (most recent membership wins). This makes the
   query deterministic even on a degraded database.

This test file pins the contract at two levels:

- The store-level test (in-memory) directly exercises the
  ``preferred_family_id`` path on the in-memory store.

- The HTTP-level test (end-to-end through the real router) creates
  two disjoint families for the same user, then asserts that
  ``GET /v1/family`` returns the family whose id matches
  ``users.family_id`` (the principal's current family), not the
  arbitrary other one. The setup is a bit contrived (the real API
  blocks creating a second family while in one — ``create_family``
  returns 409), so we drop into the store directly to inject the
  second ``family_members`` row, mirroring the production data
  state that triggered the bug.
"""

from __future__ import annotations

from ai_companion_contracts import FamilyRole

from ai_companion_api.family.store import InMemoryFamilyStore


async def _signup(ac, email: str, password: str = "pwaaaaaaaaaa") -> str:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code in (200, 201), r.text
    me = await ac.get("/v1/auth/me")
    assert me.status_code == 200
    return me.json()["user_id"]


async def test_get_family_for_user_prefers_preferred_family_id() -> None:
    """Store-level: when the user is a member of ``preferred_family_id``,
    it wins over an arbitrary other membership.

    This mirrors the production data: the user has several
    ``family_members`` rows; ``users.family_id`` is the application-level
    pointer the LLM stream checks against. The store MUST return the
    family that matches the principal's pointer.
    """
    store = InMemoryFamilyStore()
    # Family A — created first (older joined_at).
    fam_a = await store.create_family(name="Family A", owner_user_id="u-1")
    await store.add_member(
        family_id=fam_a.id,
        user_id="u-1",
        family_role=FamilyRole.owner,
        family_display_name="Alex",
        relation="self",
        color="#7c3aed",
    )
    # Family B — created later, also has u-1 as a member.
    # In production this happens when a user has older memberships
    # that were never cleaned up after disband, or when membership
    # bookkeeping drifts out of sync with users.family_id.
    fam_b = await store.create_family(name="Family B", owner_user_id="u-2")
    await store.add_member(
        family_id=fam_b.id,
        user_id="u-1",
        family_role=FamilyRole.member,
        family_display_name="Alex",
        relation="other",
        color="#7c3aed",
    )
    # The principal's family_id is fam_a (older membership, but the
    # authoritative "current" pointer). The store MUST return fam_a.
    got = await store.get_family_for_user(user_id="u-1", preferred_family_id=fam_a.id)
    assert got is not None
    assert got.id == fam_a.id

    # Same with fam_b as the preferred pointer.
    got = await store.get_family_for_user(user_id="u-1", preferred_family_id=fam_b.id)
    assert got is not None
    assert got.id == fam_b.id


async def test_get_family_for_user_deterministic_without_preferred() -> None:
    """When ``preferred_family_id`` is None, the result is the most
    recent membership (by ``joined_at DESC``) — at least deterministic.
    """
    store = InMemoryFamilyStore()
    fam_a = await store.create_family(name="Family A", owner_user_id="u-1")
    await store.add_member(
        family_id=fam_a.id,
        user_id="u-1",
        family_role=FamilyRole.owner,
        family_display_name="Alex",
        relation="self",
        color="#7c3aed",
    )
    fam_b = await store.create_family(name="Family B", owner_user_id="u-2")
    await store.add_member(
        family_id=fam_b.id,
        user_id="u-1",
        family_role=FamilyRole.member,
        family_display_name="Alex",
        relation="other",
        color="#7c3aed",
    )
    # No preferred family id — the store must return SOMETHING, and
    # that something must be deterministic.
    got1 = await store.get_family_for_user(user_id="u-1")
    got2 = await store.get_family_for_user(user_id="u-1")
    assert got1 is not None and got2 is not None
    assert got1.id == got2.id


async def test_get_family_returns_principal_current_family(make_app, app_client) -> None:
    """End-to-end: with a user who has two ``family_members`` rows, the
    HTTP ``GET /v1/family`` returns the family whose id matches
    ``users.family_id`` (the principal's current family).

    The HTTP ``create_family`` blocks creating a second family while in
    one (returns 409), so we drive the test by:

    1. Sign up a user and create family A.
    2. Directly inject a second ``family_members`` row for family B into
       the in-memory family store (the production data state that
       triggered the bug).
    3. Update ``users.family_id`` to family B (mimics a later ``accept
       invite`` / owner transfer).

    The principal's ``family_id`` is B. ``GET /v1/family`` MUST return
    B, not A — otherwise the LLM stream's body ``family_id`` will
    disagree with ``principal.family_id`` and 404.
    """
    # We need access to the live ``app`` to inject the second family
    # membership directly into the in-memory family store. The default
    # ``app_client`` fixture only yields the AsyncClient, so build one
    # inline that yields both.
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from ai_companion_api.family.store import InMemoryFamilyStore as _IFS
    from ai_companion_api.main import lifespan

    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac, app

    async with _ctx() as (ac, app):
        user_id = await _signup(ac, "stranger@x.com")
        # Create family A through the HTTP layer.
        r = await ac.post("/v1/family", json={"name": "Family A"})
        assert r.status_code == 200, r.text
        fam_a_id = r.json()["id"]

        # Inject family B and a (B, user) membership directly into the
        # in-memory store — mirroring the production data state.
        fam_store = app.state.family_store
        assert isinstance(fam_store, _IFS)
        fam_b = await fam_store.create_family(name="Family B", owner_user_id="u-other")
        await fam_store.add_member(
            family_id=fam_b.id,
            user_id=user_id,
            family_role=FamilyRole.member,
            family_display_name="Stranger",
            relation="other",
            color="#7c3aed",
        )
        # Promote the user into family B: this is the application-level
        # pointer the LLM stream checks against. (In production this
        # would happen via ``accept_invite``.)
        await app.state.auth_store.set_user_family(
            user_id=user_id,
            family_id=fam_b.id,
            family_role=FamilyRole.member.value,
        )

        # GET /v1/family — the principal's family_id is B, so the
        # response must be B, not A.
        r = await ac.get("/v1/family")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["family"]["id"] == fam_b.id
        assert body["family"]["id"] != fam_a_id
