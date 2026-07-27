"""End-to-end HTTP reproduction of the joint-family cross-member visibility
contract: member A sends a message in the shared joint thread, member B opens
the same thread and MUST see A's message.

This is the ONE layer the store-level ``test_joint_shared_visible_to_other_member``
bypasses: it proves the server glue — ``POST /v1/llm/stream`` persisting A's
turn under the family shared scope, then ``GET /v1/memory`` (the real router,
with the exact family shared query the web client sends in
``apps/web/lib/api-client.ts::listEvents``) returning A's shared row to B —
honors the joint-session contract. The insecure ``X-User-Id`` escape hatch
cannot represent family membership (it loads no user rows), so this goes
through the real auth flow: owner creates a family + invite, invitee accepts,
both verified Principals in the same family.

The reported bug ("I can't see other members' messages in the joint family
chat") was caused client-side by coupling the family scope to the BYOK key
handle; this test pins the server side so a regression in the persist or
router layer is caught even when the web client is correct.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import pytest

from ai_companion_api.auth.sessions import seal

TEST_INVITE_SECRET = "test-invite-secret-joint-http"


def _new_client_ctx(make_app, app_client):
    """Fresh app + AsyncClient (cookies enabled) sharing one app instance so
    the in-memory FamilyStore + MemoryStore persist across the owner→invitee
    session switch (owner creates family + invite, logs out, invitee signs up
    and accepts — same stores)."""

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
    """Pin AUTH_INVITE_SECRET so a token sealed before the invitee's app
    instance boots can be verified after (every create_app regenerates the
    per-app random secret otherwise)."""

    monkeypatch.setenv("AUTH_INVITE_SECRET", TEST_INVITE_SECRET)

    def _make(**env):
        env.setdefault("AUTH_INVITE_SECRET", TEST_INVITE_SECRET)
        return make_app(**env)

    return _make


async def _read_stream(ac, body: dict) -> list[dict]:
    """POST a stream request and collect the JSON event payloads in order."""
    events: list[dict] = []
    async with ac.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200, await resp.aread()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


async def test_member_b_sees_member_a_shared_message_in_joint_thread(
    make_fixed_app, app_client, monkeypatch
) -> None:
    # Force the mock adapter regardless of any LITELLM_API_KEY_* in the dev
    # shell so the turn is hermetic (no real provider call). The keyless family
    # turn falls through to mock but the event MUST still persist under the
    # family shared scope — that is what this test asserts.
    import ai_companion_api.llm.provider as prov

    real_env_key = prov._env_key

    def fake_env_key(settings, kind):  # noqa: ANN001
        return None

    prov._env_key = fake_env_key
    monkeypatch.setattr(prov, "_env_key", fake_env_key)

    captured: list[dict] = []

    class _CapturingTransport:
        async def send(self, *, to: str, subject: str, body: str) -> None:
            captured.append({"to": to, "subject": subject, "body": body})

        async def _legacy_send(self, *, to: str, link: str) -> None:
            captured.append({"to": to, "link": link})

    import ai_companion_api.auth.backends.magic_link as ml

    real_default = ml.default_transport

    def _patched_default(_settings):  # noqa: ANN001
        return _CapturingTransport()

    ml.default_transport = _patched_default
    try:
        async with _new_client_ctx(make_fixed_app, app_client) as ac:
            # --- Owner A: create family, invite B, send a joint turn. ---
            a_id = await _signup(ac, "owner@x.com")
            fam = await _new_family(ac)
            fam_id = fam["id"]
            joint_convo = f"fam-joint-{fam_id}"

            r_invite = await ac.post(
                "/v1/family/invites", json={"email": "member@x.com", "role": "member"}
            )
            assert r_invite.status_code == 200, r_invite.text
            assert captured, "invite transport.send was not called"
            body = captured[-1]["body"]
            link_line = [ln for ln in body.splitlines() if "/family/accept?token=" in ln][-1]
            token = link_line.split("token=", 1)[1].strip()

            a_msg = "A speaking in the joint thread."
            events = await _read_stream(
                ac,
                {
                    "persona_id": "fam",
                    "convo_id": joint_convo,
                    "message": a_msg,
                    "family_id": fam_id,
                    "visibility": "shared",
                    "participant_user_id": a_id,
                },
            )
            types = [e["type"] for e in events]
            assert types[0] == "session", types
            assert types[-1] == "done", types

            # --- Switch to member B (same app → same MemoryStore). ---
            await ac.post("/v1/auth/logout")
            b_id = await _signup(ac, "member@x.com")
            r_accept = await ac.post("/v1/family/accept", json={"token": token})
            assert r_accept.status_code == 200, r_accept.text
            assert r_accept.json()["family_id"] == fam_id

            # --- B opens the joint thread with the EXACT family shared filter
            # the web client sends (apps/web/lib/api-client.ts::listEvents). ---
            q = urlencode(
                {
                    "persona_id": "fam",
                    "convo_id": joint_convo,
                    "family_id": fam_id,
                    "visibility": "shared",
                    "participant_user_id": b_id,
                    "limit": "200",
                }
            )
            resp = await ac.get(f"/v1/memory?{q}")
            assert resp.status_code == 200, resp.text
            rows = resp.json()
            contents = [row.get("content") for row in rows]

            # B sees A's shared user message — the core joint-session contract.
            assert a_msg in contents, (
                f"member B's joint read MUST include member A's shared message; "
                f"got {contents}"
            )

            # And it is attributed to A (the speaker), persisted under the
            # family shared scope — not silently dropped to personal/private.
            a_row = next(row for row in rows if row.get("content") == a_msg)
            assert a_row.get("role") == "user"
            assert a_row.get("participant_user_id") == a_id, a_row
            assert a_row.get("family_id") == fam_id, a_row
            assert a_row.get("visibility") == "shared", a_row
    finally:
        ml.default_transport = real_default
        prov._env_key = real_env_key


async def test_member_b_joint_read_excludes_a_private_solo(
    make_fixed_app, app_client, monkeypatch
) -> None:
    """Defense-in-depth companion: A's PRIVATE solo disclosure (a 1:1 with the
    therapist) MUST NOT leak into B's joint read, even though both are in the
    same family. Pins that the shared-scope relaxation is gated on
    ``visibility == "shared"`` (private rows stay participant-gated)."""

    import ai_companion_api.llm.provider as prov

    real_env_key = prov._env_key
    monkeypatch.setattr(prov, "_env_key", lambda settings, kind: None)

    captured: list[dict] = []

    class _CapturingTransport:
        async def send(self, *, to: str, subject: str, body: str) -> None:
            captured.append({"body": body})

        async def _legacy_send(self, *, to: str, link: str) -> None:
            captured.append({"link": link})

    import ai_companion_api.auth.backends.magic_link as ml

    real_default = ml.default_transport
    ml.default_transport = lambda _settings: _CapturingTransport()  # noqa: E731
    try:
        async with _new_client_ctx(make_fixed_app, app_client) as ac:
            a_id = await _signup(ac, "owner@x.com")
            fam = await _new_family(ac)
            fam_id = fam["id"]
            await ac.post(
                "/v1/family/invites", json={"email": "member@x.com", "role": "member"}
            )
            body = captured[-1]["body"]
            token = [ln for ln in body.splitlines() if "/family/accept?token=" in ln][-1].split(
                "token=", 1
            )[1].strip()

            # A sends a PRIVATE solo disclosure in a solo convo.
            private_msg = "A's private disclosure to the therapist."
            solo_convo = f"fam-solo-{a_id}-1"
            events = await _read_stream(
                ac,
                {
                    "persona_id": "fam",
                    "convo_id": solo_convo,
                    "message": private_msg,
                    "family_id": fam_id,
                    "visibility": "private",
                    "participant_user_id": a_id,
                },
            )
            assert [e["type"] for e in events][-1] == "done"

            # B joins and reads the JOINT thread — A's private solo MUST NOT
            # surface (different convo + private visibility).
            await ac.post("/v1/auth/logout")
            b_id = await _signup(ac, "member@x.com")
            await ac.post("/v1/family/accept", json={"token": token})

            joint_convo = f"fam-joint-{fam_id}"
            q = urlencode(
                {
                    "persona_id": "fam",
                    "convo_id": joint_convo,
                    "family_id": fam_id,
                    "visibility": "shared",
                    "participant_user_id": b_id,
                    "limit": "200",
                }
            )
            rows = (await ac.get(f"/v1/memory?{q}")).json()
            contents = [row.get("content") for row in rows]
            assert private_msg not in contents, (
                "A's private solo disclosure MUST NOT leak into B's joint read"
            )
    finally:
        ml.default_transport = real_default
        prov._env_key = real_env_key