"""Shared pytest fixtures.

The auth middleware now gates every non-public route behind a verified Principal.
For the pre-auth test suite we enable the dev/test escape hatch
(``AUTH_ALLOW_INSECURE_USER_HEADER=1``) so an explicit ``X-User-Id`` header resolves
to an insecure Principal end-to-end. Sprint 6 M1.2 removed the implicit
``default_user_id`` fallback (a missing header no longer impersonates the default
user — it 401s, matching multi-user semantics); to keep the pre-auth tests
unchanged, the ``client`` fixture sends a default ``X-User-Id`` header set to
``settings.default_user_id``. Tests that pass their own ``X-User-Id`` (e.g.
multi-user isolation with "u1"/"u2") override it per-request. This flag is NEVER
on in production (see ``auth/middleware.py``).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

# Set before any Settings() is constructed (config.py builds one at import).
os.environ.setdefault("AUTH_ALLOW_INSECURE_USER_HEADER", "1")
# I15: rate limiting is OFF in the test suite — the tests fire many requests
# from one IP/user and would trip the per-IP/per-user caps. The limiter's
# behavior is covered by a dedicated test (tests/test_rate_limit.py) that
# re-enables it and resets the shared in-memory storage.
os.environ.setdefault("RATELIMIT_ENABLED", "0")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from ai_companion_api.main import create_app, lifespan  # noqa: E402


@pytest.fixture
async def client():
    app = create_app()
    # ASGITransport does not dispatch lifespan events, so drive the startup
    # context manually — this is what populates app.state.ecdh / app.state.settings.
    async with lifespan(app):
        transport = ASGITransport(app=app)
        # M1.2: default X-User-Id so the pre-auth suite (which relies on the
        # ``default_user_id`` principal) keeps working without the implicit
        # fallback. Per-request ``headers={"X-User-Id": ...}`` overrides this.
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-User-Id": app.state.settings.default_user_id},
        ) as ac:
            yield ac


@pytest.fixture
def make_app(monkeypatch):
    """Build an app with custom auth env (real auth — escape hatch OFF by default).

    Usage: ``app = make_app(DEPLOYMENT_MODE="hosted", AUTH_BACKEND="oidc", ...)``.
    """

    def _make(**env: object) -> object:
        env.setdefault("AUTH_ALLOW_INSECURE_USER_HEADER", "0")
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        return create_app()

    return _make


@pytest.fixture
def app_client():
    """Wrap an app (from ``make_app``) in lifespan + an httpx AsyncClient.

    ``client`` overrides the ASGITransport peer address (default loopback) so the
    trusted-header internal-origin check can be exercised against an external IP.
    """

    @asynccontextmanager
    async def _ctx(app, client=("127.0.0.1", 123), base_url="http://test"):  # type: ignore[no-untyped-def]
        async with lifespan(app):
            transport = ASGITransport(app=app, client=client)
            async with AsyncClient(transport=transport, base_url=base_url) as ac:
                yield ac

    return _ctx
