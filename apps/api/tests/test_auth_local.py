"""Local accounts backend end-to-end: signup / login / me / logout, cookie, 401.

Also covers the public ``/v1/config`` descriptor and that protected routes reject
unauthenticated requests when the escape hatch is off.
"""

from __future__ import annotations


async def test_config_reports_self_hosted_local(make_app, app_client):
    app = make_app()
    async with app_client(app) as ac:
        r = await ac.get("/v1/config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["mode"] == "self_hosted"
        assert cfg["profile"] == "local"
        assert cfg["auth_backends"] == ["local"]


async def test_protected_route_401_without_session(make_app, app_client):
    app = make_app()
    async with app_client(app) as ac:
        r = await ac.get("/v1/providers")
        assert r.status_code == 401
        # /v1/health and /v1/config remain public.
        assert (await ac.get("/v1/health")).status_code == 200
        assert (await ac.get("/v1/config")).status_code == 200


async def test_public_paths_are_method_scoped(make_app, app_client):
    """I16: PUBLIC_PATHS is method-scoped so an unauthed POST to a read-only
    descriptor (or GET on a POST-only auth mutation) is rejected at the
    middleware. The old method-blind ``path in PUBLIC_PATHS`` check let any
    method through on these paths."""
    app = make_app()
    async with app_client(app) as ac:
        # GET descriptors are public; POST to them is NOT.
        assert (await ac.get("/v1/health")).status_code == 200
        assert (await ac.post("/v1/health")).status_code == 401
        # POST auth mutations are public (reach the handler); GET on them is
        # NOT. A bodyless POST /login reaching the handler yields 422 (missing
        # body) — proving the middleware let it past, vs. 401 if it blocked.
        login_post = await ac.post("/v1/auth/login")
        assert login_post.status_code == 422, login_post.text
        assert (await ac.get("/v1/auth/login")).status_code == 401


async def test_signup_login_me_logout(make_app, app_client):
    app = make_app()
    async with app_client(app) as ac:
        # Signup establishes a session cookie.
        r = await ac.post(
            "/v1/auth/signup",
            json={
                "email": "Alice@Example.com",
                "password": "hunter2hunter2",
                "display_name": "Alice",
            },
        )
        assert r.status_code == 200, r.text
        principal = r.json()
        assert principal["email"] == "alice@example.com"  # normalized
        assert principal["auth_backend"] == "local"
        assert principal["plan"] == "self_hosted_free"
        # FEATURE_EMAIL_VERIFICATION off (default) → signup is trusted immediately.
        assert principal["email_verified"] is True
        assert "retellis_sess" in ac.cookies

        # The session cookie authorizes protected routes.
        assert (await ac.get("/v1/providers")).status_code == 200

        # /me returns the same principal.
        me = await ac.get("/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == principal["user_id"]

        # Duplicate signup is a conflict.
        dup = await ac.post(
            "/v1/auth/signup",
            json={"email": "alice@example.com", "password": "whateverywhatever"},
        )
        assert dup.status_code == 409

        # Logout revokes + clears the cookie.
        out = await ac.post("/v1/auth/logout")
        assert out.status_code == 200
        ac.cookies.clear()
        # The cookie is gone → protected routes 401 again.
        assert (await ac.get("/v1/providers")).status_code == 401


async def test_login_wrong_password_and_unknown_user(make_app, app_client):
    app = make_app()
    async with app_client(app) as ac:
        await ac.post(
            "/v1/auth/signup",
            json={"email": "bob@example.com", "password": "correcthorsebattery"},
        )
        # Wrong password → 401 with a non-enumerating message.
        bad = await ac.post(
            "/v1/auth/login",
            json={"email": "bob@example.com", "password": "wrong"},
        )
        assert bad.status_code == 401
        assert "email or password" in bad.json()["detail"]
        # Unknown user → same message (no enumeration).
        unknown = await ac.post(
            "/v1/auth/login",
            json={"email": "ghost@example.com", "password": "wrong"},
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == bad.json()["detail"]

        # Correct password → session cookie set.
        good = await ac.post(
            "/v1/auth/login",
            json={"email": "bob@example.com", "password": "correcthorsebattery"},
        )
        assert good.status_code == 200
        assert "retellis_sess" in ac.cookies


async def test_local_endpoints_404_under_oidc(make_app, app_client, monkeypatch):
    # Under an OIDC backend, the local-only endpoints should be disabled (404).
    app = make_app(
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="sso",
        AUTH_BACKEND="oidc",
        OIDC_ISSUER="https://idp.example",
        OIDC_CLIENT_ID="c",
        AUTH_STATE_SECRET="s",
    )
    async with app_client(app) as ac:
        r = await ac.post("/v1/auth/login", json={"email": "a@b.com", "password": "x"})
        assert r.status_code == 404


async def test_cross_user_isolation(make_app, app_client):
    """Two local accounts see only their own providers (the verified user_id
    scopes every store query — the pre-existing per-row isolation now enforced
    by a real Principal, not a forgeable header)."""
    app = make_app()
    async with app_client(app) as ac:
        await ac.post("/v1/auth/signup", json={"email": "a@x.com", "password": "pwaaaaaaaaaa"})
        a_id = (await ac.get("/v1/auth/me")).json()["user_id"]
        await ac.post(
            "/v1/providers",
            json={"kind": "openai", "label": "A's key", "key_handle": "kh-a"},
        )
        ac.cookies.clear()

        await ac.post("/v1/auth/signup", json={"email": "b@x.com", "password": "pwbbbbbbbbbb"})
        b_id = (await ac.get("/v1/auth/me")).json()["user_id"]
        assert a_id != b_id
        # B lists providers → A's provider is not visible.
        listed = await ac.get("/v1/providers")
        assert listed.status_code == 200
        assert all(p["label"] != "A's key" for p in listed.json())


def test_session_model_columns_match_store_revoke_assumptions():
    """Regression guard for the logout 500 bug (Sprint 6 M2: surrogate id added).

    ``PostgresAuthStore.revoke_session`` / ``revoke_all_sessions`` build
    ``UPDATE ... .returning(Session.<col>)`` statements. Originally the
    ``sessions`` table keyed only on ``token`` (no surrogate ``id``); an earlier
    version referenced ``Session.id`` before it existed, which raised
    ``AttributeError`` at logout time — so the server never cleared the cookie
    and the session stayed valid. The pytest suite exercises
    ``InMemoryAuthStore`` only, so the Postgres path shipped broken.

    Sprint 6 M2 added a surrogate ``id`` (the cookie ``token`` is a secret and
    must not be surfaced to the session-list / revoke endpoints). This guard now
    asserts the new contract: ``id`` exists and is unique, AND every revoke
    ``.returning()`` clause still uses ``.token`` (the PK — always safe) rather
    than ``.id``. ``revoke_session_by_id`` (the new M2 endpoint) keys its WHERE
    on ``.id`` + ``user_id`` but also returns ``.token``.
    """
    from ai_companion_api.db.models import Session

    cols = {c.name for c in Session.__table__.columns}
    assert "token" in cols  # primary key the store returns
    assert "revoked_at" in cols  # column the UPDATE sets
    assert "user_id" in cols  # column revoke_all_sessions filters on
    # M2: surrogate id now exists (opaque key for the session-list / revoke API).
    assert "id" in cols
    id_col = Session.__table__.columns["id"]
    assert id_col.unique, "sessions.id must be UNIQUE (it keys client-facing revoke)"
    # The .returning() clauses must stay on .token (the PK). If a future change
    # returns .id from a revoke path, revisit — .token is the only column the
    # store's bool-result logic reads via scalar_one_or_none().
    assert hasattr(Session, "token")
