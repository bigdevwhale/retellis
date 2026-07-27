"""I15: rate limiting. The suite-wide default (conftest) keeps the limiter OFF
so the many-requests-from-one-IP tests don't trip. Here we flip it back on via
``RATELIMIT_ENABLED=1`` (passed to ``make_app`` so ``create_app`` enables the
singleton limiter) against a fresh in-memory storage and assert the per-IP
auth cap actually 429s, that a different IP is NOT blocked (per-IP isolation),
and that the master switch disables it.

The limiter is a process singleton; we reset storage + disable it in teardown so
no other test is affected.
"""

from __future__ import annotations

import pytest

from ai_companion_api.ratelimit import limiter

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fresh_storage():
    """Clear the limiter's in-memory counters before and after each test."""
    limiter._storage.reset()
    yield
    limiter._storage.reset()
    limiter.enabled = False


async def test_login_rate_limited_per_ip(make_app, app_client, fresh_storage):
    app = make_app(RATELIMIT_ENABLED="1")
    # 10/minute per-IP on /v1/auth/login. Fire 10 wrong-password logins (401,
    # still counted) then assert the 11th is rate-limited (429).
    async with app_client(app) as ac:
        for _ in range(10):
            r = await ac.post(
                "/v1/auth/login", json={"email": "x@example.com", "password": "wrong"}
            )
            assert r.status_code == 401, r.text
        blocked = await ac.post(
            "/v1/auth/login", json={"email": "x@example.com", "password": "wrong"}
        )
        assert blocked.status_code == 429, blocked.text


async def test_rate_limit_is_per_ip_isolated(make_app, app_client, fresh_storage):
    app = make_app(RATELIMIT_ENABLED="1")
    # Exhaust the limit from 127.0.0.1, then show a different client IP is not
    # blocked — the cap is keyed by remote address, not global.
    async with app_client(app, client=("127.0.0.1", 123)) as ac:
        for _ in range(11):
            await ac.post("/v1/auth/login", json={"email": "x@example.com", "password": "wrong"})
        assert (
            await ac.post("/v1/auth/login", json={"email": "x@example.com", "password": "wrong"})
        ).status_code == 429
    # A different IP gets a fresh budget (still 401 — wrong password — not 429).
    async with app_client(app, client=("10.0.0.5", 999)) as ac2:
        r = await ac2.post("/v1/auth/login", json={"email": "x@example.com", "password": "wrong"})
        assert r.status_code == 401, r.text


async def test_rate_limit_master_switch_disables(make_app, app_client):
    # With the limiter disabled (the suite default), well over the cap does NOT
    # 429 — the decorators pass through.
    app = make_app()  # RATELIMIT_ENABLED defaults to 0 (conftest)
    assert limiter.enabled is False
    async with app_client(app) as ac:
        for _ in range(20):
            r = await ac.post(
                "/v1/auth/login", json={"email": "x@example.com", "password": "wrong"}
            )
            assert r.status_code == 401, r.text
