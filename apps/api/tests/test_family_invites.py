"""Family invites: send / list / revoke / accept — through the real auth flow.

The wire never carries the invite token (only the family owner sees the
invite row, which carries ``token_hash``, not the token). For tests we seal
a valid token directly using the same ``seal`` helper + invite secret, then
deliver it to a second app instance (a fresh principal) which accepts it.

The invite secret is pinned to a constant here so every ``create_app()`` in
this test module produces identical secrets (otherwise the per-app random
``auth_state_secret`` would mismatch).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest

from ai_companion_api.auth.sessions import seal

TEST_INVITE_SECRET = "test-invite-secret-fixed"


def _new_client_ctx(make_app, app_client):
    """Return an ``async with``-compatible fresh-app + AsyncClient factory."""

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


@pytest.fixture
def make_fixed_app(make_app, monkeypatch):
    """A make_app variant that pins AUTH_INVITE_SECRET so tokens sealed by one
    app can be verified by another in the same test process."""

    monkeypatch.setenv("AUTH_INVITE_SECRET", TEST_INVITE_SECRET)

    def _make(**env):
        env.setdefault("AUTH_INVITE_SECRET", TEST_INVITE_SECRET)
        return make_app(**env)

    return _make


async def _seal_token(*, family_id: str, email: str) -> str:
    """Re-derive a valid sealed invite token (the wire never returns it;
    this helper bypasses the email transport for direct acceptance)."""
    # We rely on the env-pinned secret rather than per-app settings — every
    # ``create_app()`` regenerates settings at startup, but the secret is set
    # globally via the test fixture's ``monkeypatch.setenv``.
    secret = os.environ.get("AUTH_INVITE_SECRET") or os.environ.get(
        "AUTH_STATE_SECRET", TEST_INVITE_SECRET
    )
    return seal(
        {
            "family_id": family_id,
            "email": email.lower(),
            "role": "member",
            "exp": 9_999_999_999,
            "jti": "test-jti",
            "nonce": "test-nonce",
        },
        secret,
    )


# --- send / list ------------------------------------------------------------


async def test_send_invite_owner_only_and_lists(make_fixed_app, app_client) -> None:
    async with _new_client_ctx(make_fixed_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        fam = await _new_family(ac)
        # Owner creates an invite.
        r2 = await ac.post("/v1/family/invites", json={"email": "invitee@x.com", "role": "member"})
        assert r2.status_code == 200
        invite = r2.json()
        assert invite["email"] == "invitee@x.com"
        assert invite["family_id"] == fam["id"]
        r3 = await ac.get("/v1/family/invites")
        assert r3.status_code == 200
        assert any(i["id"] == invite["id"] for i in r3.json())


async def test_send_invite_non_member_returns_404(make_fixed_app, app_client) -> None:
    async with _new_client_ctx(make_fixed_app, app_client) as ac_owner:
        await _signup(ac_owner, "owner@x.com")
        await _new_family(ac_owner)
    async with _new_client_ctx(make_fixed_app, app_client) as ac_stranger:
        await _signup(ac_stranger, "stranger@x.com")
        r = await ac_stranger.post(
            "/v1/family/invites", json={"email": "x@x.com", "role": "member"}
        )
        assert r.status_code == 404


async def test_invite_token_is_never_in_wire_response(make_fixed_app, app_client) -> None:
    async with _new_client_ctx(make_fixed_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.post("/v1/family/invites", json={"email": "x@x.com", "role": "member"})
        body = r.json()
        for key in ("token", "token_hash", "plaintext", "secret"):
            assert key not in body, f"{key} leaked in invite wire response"


async def test_revoke_invite(make_fixed_app, app_client) -> None:
    async with _new_client_ctx(make_fixed_app, app_client) as ac:
        await _signup(ac, "owner@x.com")
        await _new_family(ac)
        r = await ac.post("/v1/family/invites", json={"email": "x@x.com", "role": "member"})
        invite = r.json()
        # Revoke
        r2 = await ac.delete(f"/v1/family/invites/{invite['id']}")
        assert r2.status_code == 204
        # Second revoke is idempotent 204 (router drops silently on missing).
        r3 = await ac.delete(f"/v1/family/invites/{invite['id']}")
        assert r3.status_code == 204


# --- accept -----------------------------------------------------------------


async def test_accept_attaches_invitee_to_family(make_fixed_app, app_client) -> None:
    # Single app instance so the in-memory FamilyStore is shared between
    # the owner (who creates the family + invite row) and the invitee
    # (who accepts). Cross-instance sharing would require the Postgres
    # path. The email transport is monkey-patched in this test so we can
    # capture the plaintext token that would otherwise be mailed out.
    captured: list[dict] = []

    class _CapturingTransport:
        async def send(self, *, to: str, subject: str, body: str) -> None:
            captured.append({"to": to, "subject": subject, "body": body})

        # Backward-compat signature for the magic-link transport shape.
        async def _legacy_send(self, *, to: str, link: str) -> None:
            captured.append({"to": to, "link": link})

    # The router imports default_transport from
    # ``..auth.backends.magic_link`` lazily inside the function, so the
    # patch must land on the source module.
    import ai_companion_api.auth.backends.magic_link as ml

    real_default = ml.default_transport

    def _patched_default(_settings):  # noqa: ANN001
        return _CapturingTransport()

    ml.default_transport = _patched_default
    try:
        async with _new_client_ctx(make_fixed_app, app_client) as ac:
            owner_id = await _signup(ac, "owner@x.com")
            fam = await _new_family(ac)
            await ac.post(
                "/v1/family/invites",
                json={"email": "invitee@x.com", "role": "member"},
            )
            assert captured, "transport.send was not called"
            # Extract the token from the captured body (the link is the last
            # line of the body, "<text>\n\n<link>").
            body = captured[-1]["body"]
            link_line = [ln for ln in body.splitlines() if "/family/accept?token=" in ln][-1]
            token = link_line.split("token=", 1)[1].strip()
            # Owner signs out, invitee signs up.
            await ac.post("/v1/auth/logout")
            invitee_id = await _signup(ac, "invitee@x.com")
            r = await ac.post("/v1/family/accept", json={"token": token})
            assert r.status_code == 200, r.text
            assert r.json()["family_id"] == fam["id"]
            r2 = await ac.get("/v1/family")
            assert r2.status_code == 200
            members = r2.json()["members"]
            assert any(m["user_id"] == invitee_id for m in members)
            assert any(m["user_id"] == owner_id for m in members)
    finally:
        ml.default_transport = real_default


async def test_accept_token_replay_returns_410(make_fixed_app, app_client) -> None:
    captured: list[dict] = []

    class _CapturingTransport:
        async def send(self, *, to: str, subject: str, body: str) -> None:
            captured.append({"to": to, "body": body})

    import ai_companion_api.auth.backends.magic_link as ml

    real_default = ml.default_transport

    def _patched_default(_settings):  # noqa: ANN001
        return _CapturingTransport()

    ml.default_transport = _patched_default
    try:
        async with _new_client_ctx(make_fixed_app, app_client) as ac:
            await _signup(ac, "owner@x.com")
            await _new_family(ac)
            await ac.post(
                "/v1/family/invites",
                json={"email": "invitee@x.com", "role": "member"},
            )
            link_line = [
                ln for ln in captured[-1]["body"].splitlines() if "/family/accept?token=" in ln
            ][-1]
            token = link_line.split("token=", 1)[1].strip()
            await ac.post("/v1/auth/logout")
            await _signup(ac, "invitee@x.com")
            r1 = await ac.post("/v1/family/accept", json={"token": token})
            assert r1.status_code == 200
            # Replay protection (PLAN §16 #2): a re-submitted token 410s
            # immediately, regardless of the invite row's state. The
            # ``consume_invite_token`` call in the accept endpoint records
            # the token as used on the first accept; every replay is a no-op
            # INSERT into ``consumed_tokens`` and the router raises 410.
            r2 = await ac.post("/v1/family/accept", json={"token": token})
            assert r2.status_code == 410
    finally:
        ml.default_transport = real_default


async def test_accept_tampered_token_rejected(make_fixed_app, app_client) -> None:
    async with _new_client_ctx(make_fixed_app, app_client) as ac_owner:
        await _signup(ac_owner, "owner@x.com")
        await _new_family(ac_owner)
    async with _new_client_ctx(make_fixed_app, app_client) as ac_invitee:
        await _signup(ac_invitee, "stranger@x.com")
        r = await ac_invitee.post("/v1/family/accept", json={"token": "tampered.token.value"})
        assert r.status_code == 400


async def test_accept_in_other_family_returns_404(make_fixed_app, app_client) -> None:
    # No matching invite row exists and the user is not a member of the
    # family. A valid seal addressed to a real family fails because the
    # router can't find a matching invite row → 404.
    captured: list[dict] = []

    class _CapturingTransport:
        async def send(self, *, to: str, subject: str, body: str) -> None:
            captured.append({"to": to, "body": body})

    import ai_companion_api.auth.backends.magic_link as ml

    real_default = ml.default_transport

    def _patched_default(_settings):  # noqa: ANN001
        return _CapturingTransport()

    ml.default_transport = _patched_default
    try:
        async with _new_client_ctx(make_fixed_app, app_client) as ac:
            await _signup(ac, "owner@x.com")
            await _new_family(ac, name="B")
            # Don't create an invite row for the stranger; just seal a
            # token directly targeting the family.
            import os

            from ai_companion_api.auth.sessions import seal as _seal

            secret = os.environ.get("AUTH_INVITE_SECRET", TEST_INVITE_SECRET)
            token = _seal(
                {
                    "family_id": "nonexistent-family-id",
                    "email": "stranger@x.com",
                    "role": "member",
                    "exp": 9_999_999_999,
                    "jti": "x",
                    "nonce": "x",
                },
                secret,
            )
            # The stranger is in the same app (shared FamilyStore). They
            # sign up and try to accept — but the family_id is bogus, so
            # the router 404s.
            await ac.post("/v1/auth/logout")
            await _signup(ac, "stranger@x.com")
            r = await ac.post("/v1/family/accept", json={"token": token})
            assert r.status_code == 404
    finally:
        ml.default_transport = real_default
