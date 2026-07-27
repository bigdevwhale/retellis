"""``/v1/messengers`` router tests (no network — fake adapter).

Covers the init → bind → patch → status → delete lifecycle, cross-user
isolation (404 not 403), connect-token verification on bind, and the BYOK-blob
rejection path. The fake adapter stands in for Telegram so ``validate_token``
never hits the network and the poller (started on bind) idles harmlessly.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from ai_companion_api.main import create_app, lifespan
from ai_companion_api.messengers.base import AdapterInfo, ButtonSpec, TokenInvalid


class FakeAdapter:
    kind = "telegram"

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def validate_token(self, token: str) -> AdapterInfo:
        if token.startswith("BAD"):
            raise TokenInvalid("rejected")
        return AdapterInfo(bot_id=1, username="testbot", can_join_groups=False)

    async def poll_once(self, token, offset, *, timeout=30):  # type: ignore[no-untyped-def]
        return []

    async def send_text(self, token: str, chat_id: int, text: str) -> None:
        self.sent.append(("text", chat_id, text))

    async def send_typing(self, token: str, chat_id: int) -> None:
        self.sent.append(("typing", chat_id, None))

    async def send_inline(
        self, token: str, chat_id: int, text: str, buttons: list[list[ButtonSpec]]
    ) -> None:
        self.sent.append(("inline", chat_id, (text, buttons)))

    async def answer_callback(self, token: str, callback_id: str, text: str | None) -> None:
        self.sent.append(("callback", 0, (callback_id, text)))


@pytest.fixture
async def mclient():
    """A booted app + authed client (X-User-Id=u1) with the Telegram adapter
    swapped for FakeAdapter so no network call is made."""
    app = create_app()
    async with lifespan(app):
        fake = FakeAdapter()
        app.state.messenger_deps.adapter = fake
        app.state.adapter_registry["telegram"] = fake
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-User-Id": "u1"},
        ) as ac:
            yield ac, app


async def test_init_creates_pending_messenger(mclient) -> None:
    ac, _app = mclient
    resp = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["messenger"]["kind"] == "telegram"
    assert body["messenger"]["status"] == "pending_handshake"
    assert body["messenger"]["bot_username"] == "testbot"
    assert body["messenger"]["bot_token_masked"].endswith("xxxx")  # last 4 of token
    assert body["messenger"]["byok_bound"] is False
    assert "connect_token" in body and body["connect_token"]
    assert f"messenger={body['messenger']['id']}" in body["connect_url"]
    assert "token=" in body["connect_url"]


async def test_init_invalid_token_400(mclient) -> None:
    ac, _app = mclient
    resp = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "BAD-token-not-valid-for-telegram", "persona_id": "aria"},
    )
    assert resp.status_code == 400


async def test_init_idempotent_same_user(mclient) -> None:
    """A second init for the same user reuses the row (no fork)."""
    ac, _app = mclient
    r1 = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    r2 = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "sam"},
    )
    assert r1.json()["messenger"]["id"] == r2.json()["messenger"]["id"]
    assert r2.json()["messenger"]["persona_id"] == "sam"


async def test_bind_activates_no_byok(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    token = init.json()["connect_token"]
    resp = await ac.post(
        f"/v1/messengers/telegram/{mid}/bind?token={token}",
        json={"byok_enc_key_blob": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    assert resp.json()["byok_bound"] is False


async def test_bind_bad_connect_token_400(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    resp = await ac.post(
        f"/v1/messengers/telegram/{mid}/bind?token=not-a-real-token",
        json={"byok_enc_key_blob": None},
    )
    assert resp.status_code == 400


async def test_bind_tampered_byok_blob_400(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    token = init.json()["connect_token"]
    resp = await ac.post(
        f"/v1/messengers/telegram/{mid}/bind?token={token}",
        json={"byok_enc_key_blob": "!!!not-a-valid-sealed-blob!!!"},
    )
    assert resp.status_code == 400


async def test_list_messengers(mclient) -> None:
    ac, _app = mclient
    await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    resp = await ac.get("/v1/messengers")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["kind"] == "telegram"


async def test_patch_persona(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    resp = await ac.patch(f"/v1/messengers/{mid}", json={"persona_id": "sam"})
    assert resp.status_code == 200
    assert resp.json()["persona_id"] == "sam"


async def test_patch_pause_then_resume(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    token = init.json()["connect_token"]
    await ac.post(f"/v1/messengers/telegram/{mid}/bind?token={token}", json={"byok_enc_key_blob": None})
    paused = await ac.patch(f"/v1/messengers/{mid}", json={"status": "paused"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    resumed = await ac.patch(f"/v1/messengers/{mid}", json={"status": "active"})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"


async def test_status_endpoint(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    resp = await ac.get(f"/v1/messengers/{mid}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_handshake"
    assert resp.json()["byok_bound"] is False


async def test_delete_messenger(mclient) -> None:
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    resp = await ac.delete(f"/v1/messengers/{mid}")
    assert resp.status_code == 204
    # Idempotent: deleting again is still 204.
    again = await ac.delete(f"/v1/messengers/{mid}")
    assert again.status_code == 204
    listing = await ac.get("/v1/messengers")
    assert listing.json() == []


async def test_cross_user_isolated_404(mclient) -> None:
    """u1's messenger must not be visible/deletable as u2 (404, not 403)."""
    ac, _app = mclient
    init = await ac.post(
        "/v1/messengers/telegram",
        json={"bot_token": "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx", "persona_id": "aria"},
    )
    mid = init.json()["messenger"]["id"]
    # Same transport, but act as u2.
    ac.headers["X-User-Id"] = "u2"
    assert (await ac.get("/v1/messengers")).json() == []
    assert (await ac.get(f"/v1/messengers/{mid}/status")).status_code == 404
    assert (await ac.patch(f"/v1/messengers/{mid}", json={"persona_id": "sam"})).status_code == 404
    assert (await ac.delete(f"/v1/messengers/{mid}")).status_code == 204  # idempotent 204