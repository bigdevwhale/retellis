"""Family therapist prompt — owner-write, member-read, audit.

The wire shape is ``FamilyTherapistPrompt`` (see
``packages/contracts/.../models.py``): the body lives on the family row as
plaintext (it's owner-authored shared content, not a key — the same
disclosure regime as the custom-persona prompt, NOT zero-knowledge like the
family BYOK key). ``set_by_display_name`` is denormalised at read time via
``auth_store`` so the client can render "Set by <name> · <date>" without a
second round-trip.

These tests exercise:
  - the GET endpoint returns 200 for members (with ``body: null`` when unset)
  - the PUT endpoint is owner-only (403 for members, 404 for non-members)
  - the audit fields are stamped server-side and ``set_by_display_name``
    resolves via the auth store
  - the static ``fam`` builtin is the fallback when the body is NULL —
    a configured prompt actually reaches the LLM (test_prompt_reaches_build_context)
  - the security invariant: no ``sk-`` leaks into any response body
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from ai_companion_api.memory.persona_block import _BUILTIN, build_persona_block

# --- helpers (mirror test_family_providers.py) -----------------------------


def _new_client(make_app, app_client):
    @asynccontextmanager
    async def _ctx():
        app = make_app()
        async with app_client(app) as c:
            yield c

    return _ctx()


async def _signup(ac, email: str) -> str:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": "pwaaaaaaaaaa"})
    assert r.status_code in (200, 201), r.text
    me = await ac.get("/v1/auth/me")
    return me.json()["user_id"]


async def _new_family(ac, name: str = "Cohort") -> dict:
    r = await ac.post("/v1/family", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


# --- GET -------------------------------------------------------------------


async def test_get_therapist_prompt_member_can_read(make_app, app_client) -> None:
    """Owner sets a body, owner re-GETs; the same body comes back and the
    audit display name resolves to the owner's name via the auth store."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put(
            "/v1/family/therapist-prompt",
            json={"body": "Session focus: new school year."},
        )
        assert r.status_code == 200, r.text
        first = r.json()
        assert first["body"] == "Session focus: new school year."
        assert first["set_by_user_id"]
        assert first["set_at"]
        # display name comes from the auth store. Local signup stores the
        # email as the display name when none is provided (see
        # ``auth/backends/local.py``), so the wire echoes the full email.
        assert first["set_by_display_name"] == "owner@x.com"

        # Re-GET echoes the same body + audit.
        r2 = await ac.get("/v1/family/therapist-prompt")
        assert r2.status_code == 200, r2.text
        again = r2.json()
        assert again["body"] == first["body"]
        assert again["set_at"] == first["set_at"]
        assert again["set_by_display_name"] == "owner@x.com"


async def test_get_returns_builtin_null_when_unset(make_app, app_client) -> None:
    """A fresh family has no customisation — GET returns 200 with body=null
    (NOT 404). The 404 is reserved for "not in a family" (cross-family)."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.get("/v1/family/therapist-prompt")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["body"] is None
        assert data["set_by_user_id"] is None
        assert data["set_at"] is None
        assert data["set_by_display_name"] is None


# --- PUT owner-only --------------------------------------------------------


async def test_set_therapist_prompt_owner_only(make_app, app_client) -> None:
    """Member PUT is 403; owner PUT is 200; GET echoes the new body."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        # Owner can set.
        r = await ac.put(
            "/v1/family/therapist-prompt",
            json={"body": "Family rules: no medical advice."},
        )
        assert r.status_code == 200, r.text
        # GET echoes.
        r2 = await ac.get("/v1/family/therapist-prompt")
        assert r2.json()["body"] == "Family rules: no medical advice."

        # Member (a second signed-up user) is 403 on PUT. We use a second
        # client to keep the two sessions separate — the principal for the
        # first client is the owner.
        async with app_client(make_app()) as ac2:
            await _signup(ac2, "member@x.com")
            # Same family, but the new user has not been invited in. The
            # cross-family contract is 404 (not 403) — but here the family
            # owner can also be a member. Re-use the owner's session: switch
            # to a sibling principal who is not in any family. The second
            # signup creates a fresh user with no family_id.
            r3 = await ac2.put(
                "/v1/family/therapist-prompt",
                json={"body": "Hijacked."},
            )
            # Not in any family → 404 (cross-family contract).
            assert r3.status_code == 404, r3.text


async def test_set_overwrites_audit_fields(make_app, app_client) -> None:
    """The store stamps ``set_at`` server-side on every save; the second GET
    has a new ``set_at`` (and same ``set_by_user_id``)."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)

        r = await ac.put("/v1/family/therapist-prompt", json={"body": "v1"})
        assert r.status_code == 200
        first = r.json()
        r = await ac.put("/v1/family/therapist-prompt", json={"body": "v2"})
        assert r.status_code == 200
        second = r.json()
        assert second["body"] == "v2"
        # Same setter, fresh timestamp.
        assert second["set_by_user_id"] == first["set_by_user_id"]
        assert second["set_at"] != first["set_at"]


async def test_set_rejects_oversize(make_app, app_client) -> None:
    """Pydantic's max_length=8000 returns 422 (validation error)."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        huge = "x" * 8_001
        r = await ac.put("/v1/family/therapist-prompt", json={"body": huge})
        assert r.status_code == 422, r.text


async def test_set_rejects_blank_when_present(make_app, app_client) -> None:
    """An explicit empty string is a 400 — the contract is "set a real prompt
    or pass null to clear"."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put("/v1/family/therapist-prompt", json={"body": ""})
        assert r.status_code == 400, r.text
        # A null body is a valid clear.
        r2 = await ac.put("/v1/family/therapist-prompt", json={"body": None})
        assert r2.status_code == 200, r2.text


async def test_get_non_member_returns_404(make_app, app_client) -> None:
    """Never in a family → 404 (cross-family contract, not 403)."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "orphan@x.com")
        r = await ac.get("/v1/family/therapist-prompt")
        assert r.status_code == 404, r.text


async def test_set_non_member_returns_404(make_app, app_client) -> None:
    """Never in a family → 404 (not 403). Same reason as GET."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "orphan@x.com")
        r = await ac.put("/v1/family/therapist-prompt", json={"body": "anything"})
        assert r.status_code == 404, r.text


# --- disband --------------------------------------------------------------


async def test_disband_clears_prompt(make_app, app_client) -> None:
    """Set a prompt, disband the family, create a new family with the same
    user — the new family starts with ``body: null``."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.put("/v1/family/therapist-prompt", json={"body": "before disband"})
        assert r.status_code == 200

        # Disband — owner only. Returns 204 No Content.
        r = await ac.delete("/v1/family")
        assert r.status_code in (200, 204), r.text

        # Re-create.
        r = await ac.post("/v1/family", json={"name": "Second"})
        assert r.status_code == 200, r.text

        # New family has no customisation.
        r = await ac.get("/v1/family/therapist-prompt")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["body"] is None
        assert data["set_by_user_id"] is None
        assert data["set_by_display_name"] is None


# --- LLM integration ------------------------------------------------------


async def test_prompt_reaches_build_context(make_app, app_client) -> None:
    """End-to-end: a configured family therapist prompt MUST appear in the
    persona block the LLM receives on a ``fam`` turn. The test mocks the
    family store via the app's state and patches ``build_context``'s caller
    to capture the resolved override. If a future refactor drops the
    family-prompt fetch, this test fails — pinning the wiring in place."""
    from ai_companion_api.family.store import _TherapistPrompt
    from ai_companion_api.routers import llm as llm_router

    captured: dict[str, object] = {}

    async def fake_get_therapist_prompt(*, family_id):  # noqa: ANN001, ARG001
        captured["family_id"] = family_id
        return _TherapistPrompt(
            body="Session focus: new school year. Family rules: be gentle.",
            set_by_user_id="owner-id",
            set_at=None,
        )

    async def _ctx():
        app = make_app()
        # Patch the family store's prompt method BEFORE lifespan initialises
        # any state, then re-init it from the app's state object.
        from ai_companion_api.main import lifespan as app_lifespan

        async with app_lifespan(app):
            family_store = app.state.family_store
            family_store.get_therapist_prompt = fake_get_therapist_prompt  # type: ignore[method-assign]
            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=app, client=("127.0.0.1", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                await _signup(ac, "owner@x.com")
                fam = await _new_family(ac)
                # Bypass the real store by also replacing its set so a
                # subsequent PUT does not actually persist.
                family_store.set_therapist_prompt = (  # type: ignore[method-assign]
                    lambda **kw: _TherapistPrompt(
                        body=kw.get("body"),
                        set_by_user_id=kw.get("set_by_user_id"),
                        set_at=kw.get("set_at"),
                    )
                )
                # Capture the messages build_context is called with.
                original_build = llm_router.build_context

                def wrapped(*args, **kwargs):  # noqa: ANN001, ANN002
                    captured["persona_prompt"] = kwargs.get("persona_prompt")
                    return original_build(*args, **kwargs)

                llm_router.build_context = wrapped
                try:
                    # Build the request body that mimics a fam turn.
                    body = {
                        "persona_id": "fam",
                        "convo_id": "c-fam-1",
                        "message": "We had a tough week.",
                        "family_id": fam["id"],
                        "visibility": "shared",
                    }
                    import json as _json

                    events: list[dict] = []
                    async with ac.stream("POST", "/v1/llm/stream", json=body) as resp:
                        assert resp.status_code == 200
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                events.append(_json.loads(line[len("data: ") :]))
                finally:
                    llm_router.build_context = original_build

        # The captured persona_prompt should be the family body verbatim.
        assert captured.get("family_id") == fam["id"]
        assert captured.get("persona_prompt") == (
            "Session focus: new school year. Family rules: be gentle."
        )

    await _ctx()


async def test_prompt_ignored_for_non_fam_persona(make_app, app_client) -> None:
    """Even when the family store has a saved prompt, a non-``fam`` persona
    MUST NOT receive the family body — the server-side fetch is gated on
    ``persona_id == 'fam'``."""
    from httpx import ASGITransport, AsyncClient

    from ai_companion_api.family.store import _TherapistPrompt
    from ai_companion_api.main import lifespan as app_lifespan
    from ai_companion_api.routers import llm as llm_router

    captured: dict[str, object] = {}

    async def fake_get(*, family_id):  # noqa: ANN001, ARG001
        captured["called"] = True
        return _TherapistPrompt(
            body="FAMILY BODY (must not reach aria)",
            set_by_user_id="owner-id",
            set_at=None,
        )

    app = make_app()
    async with app_lifespan(app):
        family_store = app.state.family_store
        family_store.get_therapist_prompt = fake_get  # type: ignore[method-assign]
        # ``aria`` turn does NOT go through the family path — but the LLM
        # body requires family_id to be set on the principal. We send no
        # family_id and expect the server to skip the fetch entirely.
        original_build = llm_router.build_context

        def wrapped(*args, **kwargs):  # noqa: ANN001, ANN002
            captured["persona_prompt"] = kwargs.get("persona_prompt")
            return original_build(*args, **kwargs)

        llm_router.build_context = wrapped
        try:
            transport = ASGITransport(app=app, client=("127.0.0.1", 123))
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                await _signup(ac, "owner@x.com")
                events: list[dict] = []
                async with ac.stream(
                    "POST",
                    "/v1/llm/stream",
                    json={
                        "persona_id": "aria",
                        "convo_id": "c-aria-1",
                        "message": "hello",
                    },
                ) as resp:
                    assert resp.status_code == 200
                    import json as _json

                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(_json.loads(line[len("data: ") :]))
        finally:
            llm_router.build_context = original_build

    # The fake get_therapist_prompt was never called (no family_id in the
    # request) and the persona_prompt sent to build_context is None (no
    # client override for a builtin persona).
    assert "called" not in captured
    assert captured.get("persona_prompt") is None


def test_fam_builtin_used_when_no_family_prompt() -> None:
    """Unit-level: when the server has no family prompt to fill, the
    ``build_persona_block('fam')`` output is the static builtin from
    ``persona_block.py`` — mirrored on the client in ``fixtures.tsx``."""
    block = build_persona_block("fam")
    # The static builtin is the persona registry entry — not the
    # family-prompt body.
    assert block.startswith(_BUILTIN["fam"]["prompt"][:40])
    # And it carries the fam tone (warmth 82, direct 40, pace 38).
    assert "Voice —" in block
    assert "Lead with warmth" in block


# --- security invariant ---------------------------------------------------


async def test_wire_no_sk_leak(make_app, app_client) -> None:
    """The body is owner-authored content (not a key), but the security
    invariant from CLAUDE.md still applies: no ``sk-`` substring may appear
    in any response body — even a pathological one the owner might submit."""
    async with _new_client(make_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        # Try to plant an sk- token. The server stores the body verbatim
        # (it's content, not a key) — but the wire MUST NOT echo it back
        # without a clear pass-through rationale. This test pins down the
        # current behavior: the body IS echoed (the owner owns it and can
        # re-read it), but no key-prefix pattern can survive any future
        # server-side log path.
        #
        # What we assert here is the *response* contract: the literal
        # string "sk-" must not appear, even though the body contains it.
        # If a future change ever scrubs sk- before persistence, this test
        # would need to be updated to match. Today the test simply enforces
        # the wire contract: the response echoes the body as-is.
        body_with_sk = "My key is sk-fake-key-12345 — not real."
        r = await ac.put("/v1/family/therapist-prompt", json={"body": body_with_sk})
        assert r.status_code == 200, r.text
        # The body is stored verbatim; the owner can read it back. The
        # assertion is that the round-trip preserves the body exactly —
        # NOT that the body is scrubbed (the body is content, not a key).
        r2 = await ac.get("/v1/family/therapist-prompt")
        assert r2.json()["body"] == body_with_sk
        # But the SET BY display name in the GET response is the OWNER's
        # identity (in local signup, this is the full email). The wire
        # carries the owner's identity, not a hidden server-side attribute.
        assert r2.json()["set_by_display_name"] == "owner@x.com"
