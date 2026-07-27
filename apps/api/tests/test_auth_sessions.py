"""Sprint 6 M2 — session management: list + revoke by surrogate id.

``GET /v1/auth/sessions`` returns the caller's active (non-revoked) sessions
keyed by the opaque surrogate ``id`` (never the cookie ``token``, which is a
secret). ``DELETE /v1/auth/sessions/{id}`` revokes one; ``DELETE
/v1/auth/sessions`` revokes all EXCEPT the current session. Cross-user revoke
is a 404 (not 403) — the store filters on ``user_id AND id`` so another user's
session id simply doesn't match.

These use the real auth path (escape hatch OFF): signup/login establish real
session cookies. Multi-session tests build ONE app (one in-memory auth store)
and wrap several httpx clients around its ASGITransport so the same user account
underlies every session — ``make_app()`` creates a fresh store each call, so two
``_new_client`` contexts would not share users.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

from httpx import ASGITransport, AsyncClient

from ai_companion_api.main import create_app, lifespan

EMAIL = "owner@x.com"
PW = "pwaaaaaaaaaa"


async def _signup(ac, email: str = EMAIL) -> None:
    r = await ac.post("/v1/auth/signup", json={"email": email, "password": PW})
    assert r.status_code in (200, 201), r.text


@asynccontextmanager
async def _clients(make_app, n: int):
    """One app, n httpx clients (each its own cookie jar) on one ASGITransport."""
    app = make_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncExitStack() as stack:
            clients = [
                await stack.enter_async_context(
                    AsyncClient(transport=transport, base_url="http://test")
                )
                for _ in range(n)
            ]
            yield clients, app


async def test_list_sessions_returns_only_current(make_app) -> None:
    async with _clients(make_app, 1) as (clients, _app):
        ac = clients[0]
        await _signup(ac)
        r = await ac.get("/v1/auth/sessions")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        row = rows[0]
        # The session token (secret) is never surfaced.
        assert "token" not in row
        assert {"id", "created_at", "expires_at", "user_agent", "current"} <= set(row)
        assert row["current"] is True


async def test_revoke_other_session_ends_it(make_app) -> None:
    # Two clients, same user (same app/store) → two sessions. Revoking the
    # other session by id makes that client's cookie invalid (its /auth/me → 401).
    async with _clients(make_app, 2) as (clients, _app):
        ac1, ac2 = clients
        await _signup(ac1)
        login = await ac2.post("/v1/auth/login", json={"email": EMAIL, "password": PW})
        assert login.status_code in (200, 201), login.text

        rows = (await ac1.get("/v1/auth/sessions")).json()
        assert len(rows) == 2
        other = next(r for r in rows if not r["current"])
        assert other["current"] is False

        del_r = await ac1.delete(f"/v1/auth/sessions/{other['id']}")
        assert del_r.status_code == 204

        # ac2's session is now dead; ac1's is still alive.
        assert (await ac2.get("/v1/auth/me")).status_code == 401
        assert (await ac1.get("/v1/auth/me")).status_code == 200


async def test_revoke_current_session_rejected(make_app) -> None:
    # Revoking the CURRENT session from its own card is a 409 — use logout so
    # the cookie is also cleared (a dead-row + live-cookie half-state is
    # confusing). 409, not 404: the row exists and is the caller's.
    async with _clients(make_app, 1) as (clients, _app):
        ac = clients[0]
        await _signup(ac)
        rows = (await ac.get("/v1/auth/sessions")).json()
        current = next(r for r in rows if r["current"])
        r = await ac.delete(f"/v1/auth/sessions/{current['id']}")
        assert r.status_code == 409


async def test_revoke_unknown_session_id_404(make_app) -> None:
    async with _clients(make_app, 1) as (clients, _app):
        ac = clients[0]
        await _signup(ac)
        r = await ac.delete("/v1/auth/sessions/does-not-exist-id")
        assert r.status_code == 404


async def test_revoke_all_other_sessions(make_app) -> None:
    # Three sessions for one user; DELETE /v1/auth/sessions (no id) revokes all
    # except the current one and returns the count.
    async with _clients(make_app, 3) as (clients, _app):
        ac1, ac2, ac3 = clients
        await _signup(ac1)
        assert (
            await ac2.post("/v1/auth/login", json={"email": EMAIL, "password": PW})
        ).status_code in (200, 201)
        assert (
            await ac3.post("/v1/auth/login", json={"email": EMAIL, "password": PW})
        ).status_code in (200, 201)
        assert len((await ac1.get("/v1/auth/sessions")).json()) == 3

        r = await ac1.delete("/v1/auth/sessions")
        assert r.status_code == 200
        assert r.json()["revoked"] == 2

        assert (await ac2.get("/v1/auth/me")).status_code == 401
        assert (await ac3.get("/v1/auth/me")).status_code == 401
        assert (await ac1.get("/v1/auth/me")).status_code == 200
        rows = (await ac1.get("/v1/auth/sessions")).json()
        assert len(rows) == 1
        assert rows[0]["current"] is True


async def test_cross_user_session_not_listed_not_revokable(make_app) -> None:
    # User A cannot see or revoke user B's session by guessing its id — the
    # store scopes list + revoke to the caller's user_id.
    async with _clients(make_app, 2) as (clients, _app):
        a, b = clients
        await _signup(a, "a@x.com")
        await _signup(b, "b@x.com")
        a_rows = (await a.get("/v1/auth/sessions")).json()
        b_rows = (await b.get("/v1/auth/sessions")).json()
        assert {r["id"] for r in a_rows}.isdisjoint({r["id"] for r in b_rows})
        for bid in [r["id"] for r in b_rows]:
            assert (await a.delete(f"/v1/auth/sessions/{bid}")).status_code == 404
        # B's session is still alive.
        assert (await b.get("/v1/auth/me")).status_code == 200


async def test_user_agent_captured_on_signup(make_app) -> None:
    # M2: the User-Agent at signup is stored on the session row and surfaced in
    # the session list (so the user can recognize the device). httpx always
    # sends a default UA, so we assert the EXPLICIT UA we set is the one stored.
    async with _clients(make_app, 1) as (clients, _app):
        ac = clients[0]
        r = await ac.post(
            "/v1/auth/signup",
            json={"email": EMAIL, "password": PW},
            headers={"User-Agent": "Mozilla/5.0 (TestDevice) Chrome/120"},
        )
        assert r.status_code in (200, 201), r.text
        rows = (await ac.get("/v1/auth/sessions")).json()
        assert len(rows) == 1
        assert rows[0]["user_agent"] is not None
        assert "TestDevice" in rows[0]["user_agent"]


async def test_sessions_endpoint_requires_auth(make_app) -> None:
    # No cookie → 401 (the session endpoints are NOT in PUBLIC_PATHS).
    app = make_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            assert (await ac.get("/v1/auth/sessions")).status_code == 401


# ``create_app`` is imported to keep the module self-contained for tooling that
# scans for the app factory; the tests above build via ``make_app`` instead.
_ = create_app
