"""Hosted credits gate — out-of-credits ⇒ skip real providers, serve mock.

In hosted mode, a Principal with ``credits_usd <= 0`` gates the routing chain
*before* any real provider runs: the env-fallback OpenAI candidate is skipped and
the turn is served by mock with a ``fallback`` event whose reason is
``"out of credits"``. Self-hosted never consults credits (covered by the existing
budget tests). Metering (credit decrement) is best-effort and must not break the
turn.
"""

from __future__ import annotations

import json
import urllib.parse

from ai_companion_api.auth.backends.magic_link import MagicLinkBackend


class _CaptureTransport:
    def __init__(self):
        self.links: list[tuple[str, str]] = []

    async def send(self, *, to: str, link: str) -> None:
        self.links.append((to, link))


async def _read_events(client, body: dict) -> list[dict]:
    events: list[dict] = []
    async with client.stream("POST", "/v1/llm/stream", json=body) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


async def test_out_of_credits_gates_real_provider(make_app, app_client):
    app = make_app(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="magic_link",
        AUTH_MAGIC_LINK_SECRET="ml-secret",
        AUTH_EMAIL_TRANSPORT="console",
        HOSTED_SIGNUP_CREDITS_USD="0",
        # Hosted must boot over https (bootstrap rejects an http origin so the
        # session cookie is Secure).
        PUBLIC_ORIGIN="https://app.example.com",
        # A real env-fallback candidate so the chain has something to skip.
        LITELLM_API_KEY_OPENAI="sk-fake-not-used",
        MONTHLY_BUDGET_USD="1000",  # keep the monthly budget gate off
    )
    async with app_client(app, base_url="https://test") as ac:
        # Sign in via magic link → a hosted principal with 0 credits.
        capture = _CaptureTransport()
        app.state.auth_backend = MagicLinkBackend(
            app.state.settings, app.state.auth_store, transport=capture
        )
        await ac.post("/v1/auth/magiclink", json={"email": "hosted@example.com"})
        assert capture.links
        token = urllib.parse.parse_qs(urllib.parse.urlparse(capture.links[0][1]).query)["token"][0]
        cb = await ac.get("/v1/auth/magiclink/verify", params={"token": token})
        assert cb.status_code == 303
        me = await ac.get("/v1/auth/me")
        assert me.json()["credits_usd"] == 0
        assert me.json()["plan"] == "hosted_free"

        events = await _read_events(
            ac,
            {"persona_id": "lou", "convo_id": "c1", "message": "hi", "memory_on": False},
        )
        types = [e["type"] for e in events]
        assert types[0] == "session"
        assert types[-1] == "done"
        # The gate fires: a fallback event with reason "out of credits".
        fallbacks = [e for e in events if e["type"] == "fallback"]
        assert any(f["reason"] == "out of credits" for f in fallbacks), events
        # The turn was served by mock (the real provider was skipped).
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["provider_kind"] == "mock"
        # And a real reply still came back.
        assert "".join(e["text"] for e in events if e["type"] == "token").strip()


async def test_self_hosted_ignores_credits(make_app, app_client):
    """Self-hosted must NOT gate on credits even when the principal has 0 — the
    monthly budget meter is the only gate there."""
    app = make_app(
        DEPLOYMENT_MODE="self_hosted",
        AUTH_SELF_HOSTED_PROFILE="local",
        AUTH_BACKEND="local",
        LITELLM_API_KEY_OPENAI="sk-fake-not-used",
        MONTHLY_BUDGET_USD="1000",
    )
    async with app_client(app) as ac:
        await ac.post(
            "/v1/auth/signup",
            json={"email": "sh@example.com", "password": "pwshshshshsh"},
        )
        events = await _read_events(
            ac,
            {"persona_id": "lou", "convo_id": "c1", "message": "hi", "memory_on": False},
        )
        fallbacks = [e for e in events if e["type"] == "fallback"]
        assert not any(f["reason"] == "out of credits" for f in fallbacks)
