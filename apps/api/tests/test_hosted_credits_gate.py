"""Hosted credits gate — out-of-credits ⇒ skip real providers, serve mock.

In hosted mode, a Principal with ``credits_usd <= 0`` gates the routing chain
*before* any real provider runs: the env-fallback OpenAI candidate is skipped and
the turn is served by mock with a ``fallback`` event whose reason is
``"out of credits"``. Self-hosted never consults credits (covered by the existing
budget tests). Metering (credit decrement) is best-effort and must not break the
turn.
"""

from __future__ import annotations

import base64
import json
import urllib.parse

from nacl.public import PublicKey, SealedBox

from ai_companion_api.auth.backends.magic_link import MagicLinkBackend


def _seal_key(payload: dict, pub_b64: str) -> str:
    """ECDH-seal a key JSON payload to the server session pubkey (libsodium
    ``crypto_box_seal`` — the same primitive the client uses at onboarding)."""
    pub = PublicKey(base64.b64decode(pub_b64))
    return base64.b64encode(SealedBox(pub).encrypt(json.dumps(payload).encode("utf-8"))).decode(
        "ascii"
    )


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


async def test_out_of_credits_returns_402(make_app, app_client):
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

        # Out of credits should return HTTP 402 (Payment Required)
        resp = await ac.post(
            "/v1/llm/stream",
            json={"persona_id": "lou", "convo_id": "c1", "message": "hi", "memory_on": False},
        )
        assert resp.status_code == 402
        body = resp.json()
        assert "credits" in body["detail"].lower() or "out of" in body["detail"].lower()
        # No key material leaks in error response.
        assert "sk-" not in json.dumps(body)


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


async def test_out_of_credits_keeps_byok(make_app, app_client, monkeypatch):
    """A free hosted user (credits=0) with their own BYOK key is NOT cut to mock.

    The credits gate paywalls only operator-provided env-fallback / Ollama nodes —
    BYOK is the user's own key and consumes no operator credits, so it must keep
    working. BYOK serves the turn and no 'out of credits' gate fallback is emitted.
    (Contrast ``test_out_of_credits_gates_real_provider`` above: that user has no
    BYOK key, so env-fallback is dropped and mock serves.)"""
    from ai_companion_api.llm.litellm_adapter import LiteLLMAdapter
    from ai_companion_api.llm.types import LlmUsage

    async def _fake_stream(self, messages, model):  # noqa: ANN001
        # Avoid litellm / any network: yield one canned token + populate usage so
        # the turn completes on BYOK without a real provider call.
        yield "ok"
        self._usage = LlmUsage(self.provider_kind, model, 1, 1, 0.0)

    monkeypatch.setattr(LiteLLMAdapter, "stream", _fake_stream)

    dek = base64.b64encode(b"k" * 32).decode()
    app = make_app(
        DEPLOYMENT_MODE="hosted",
        AUTH_BACKEND="local",
        HOSTED_SIGNUP_CREDITS_USD="0",
        PUBLIC_ORIGIN="https://app.example.com",
        MESSENGER_TOKEN_DEK=dek,
        # No env-fallback key → the only real candidate is the user's BYOK key.
        MONTHLY_BUDGET_USD="1000",
    )
    async with app_client(app, base_url="https://test") as ac:
        await ac.post("/v1/auth/signup", json={"email": "h@x.com", "password": "pwaaaaaaaaaa"})
        me = await ac.get("/v1/auth/me")
        assert me.json()["credits_usd"] == 0
        assert me.json()["plan"] == "hosted_free"

        # Store a BYOK provider key (ECDH-sealed once → server envelope-encrypts it).
        pub_b64 = (await ac.get("/v1/health")).json()["ecdh_pub"]
        blob = _seal_key(
            {"provider_kind": "openai", "api_key": "sk-byok-test-1234567890", "base_url": None},
            pub_b64,
        )
        r = await ac.post(
            "/v1/providers",
            json={"kind": "openai", "label": "Mine", "key_handle": "kh-1", "enc_key_blob": blob},
        )
        assert r.status_code == 200, r.text

        # New-client per-turn path: enc_key_blob=None + key_handle → resolve from
        # the envelope store. The gate keeps BYOK (credits=0 doesn't cut it).
        events = await _read_events(
            ac,
            {
                "persona_id": "lou",
                "convo_id": "c1",
                "message": "hi",
                "memory_on": False,
                "key_handle": "kh-1",
                "enc_key_blob": None,
            },
        )
        types = [e["type"] for e in events]
        assert types[0] == "session"
        assert types[-1] == "done"
        fallbacks = [e for e in events if e["type"] == "fallback"]
        assert not any(f["reason"] == "out of credits" for f in fallbacks), events
        # BYOK served the turn (not mock).
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["provider_kind"] == "openai"
        assert "".join(e["text"] for e in events if e["type"] == "token").strip() == "ok"
