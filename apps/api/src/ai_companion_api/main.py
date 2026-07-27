"""FastAPI entrypoint for the Stillside API.

Phase 1 added ``/v1/health`` + the server X25519 session keypair. Phase 2 added
the BYOK vault decrypt path, ``/v1/providers`` (key_handle only), and the
streaming ``/v1/llm/stream`` SSE endpoint with BYOK → env → mock precedence and
log redaction. Phase 3 adds the event-chain memory store, wires ``/v1/llm/stream``
to recall + persist events + usage, and exposes ``/v1/memory`` + recall.
Phase 4 adds the fallback chain (BYOK → env → Ollama → mock), monthly budget
enforcement (soft-warn 80% / hard-stop 100%), and ``GET /v1/routing`` for the
routing + budget dashboard.

Phase 5 adds the auth & deployment-mode layer: a verified ``Principal`` resolved
from a session cookie, a pluggable ``AuthBackend`` (local / OIDC / magic-link /
trusted-header), the boot-time mode→backend matrix, ``/v1/auth/*`` + ``/v1/config``,
and the hosted credits gate. Auth identity is decoupled from the BYOK vault.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .auth import router as auth_router
from .auth.backends import AuthError, build_backend
from .auth.bootstrap import AuthConfigError, validate_auth_config
from .auth.invite_secret import ensure_invite_secret
from .auth.middleware import AuthMiddleware
from .auth.store import InMemoryAuthStore, make_auth_store
from .billing.store import InMemoryBillingStore, make_billing_store
from .config import Settings
from .crypto.envelope import make_envelope
from .family.store import FamilyStoreError, InMemoryFamilyStore, make_family_store
from .memory.store import InMemoryStore, make_store
from .messengers.polling import MessengerPoller, PollerDeps
from .messengers.registry import build_adapter_registry
from .messengers.store import InMemoryMessengerStore, make_messenger_store
from .observability import install_redaction
from .ratelimit import limiter
from .routers import billing, family, health, journal, llm, memory, messengers, providers, routing
from .vault.session_ecdh import generate_session_keypair

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Settings + server session keypair live for the lifetime of the process.
    settings = Settings()
    app.state.settings = settings
    app.state.ecdh = generate_session_keypair()
    # Family-invite signing key. If the operator didn't set AUTH_INVITE_SECRET
    # or AUTH_STATE_SECRET (the local self-hosted+local profile doesn't need
    # either), generate a persistent one on first boot. Without this, the
    # family-invite flow is dead-on-arrival: ``open_sealed`` rejects any token
    # signed with an empty secret (400 "invalid invite token"), and the
    # invitee never gets attached → cascading 404 on GET /v1/family.
    ensure_invite_secret(settings=settings)
    # Event + usage store: Postgres when COMPANION_USE_DB=1, else in-memory.
    store = make_store(settings)
    # If Postgres was requested, verify the schema actually exists. Alembic runs
    # best-effort in the container CMD; if it failed (e.g. the events table
    # never got created), PostgresStore would 500 on every memory read/write.
    # Fall back to the in-memory store so the app degrades gracefully
    # (session-scoped memory) and log loudly so the operator fixes migrations.
    #
    # I20: probe EVERY critical table, not just ``events``. A partially-applied
    # migration (e.g. 0011 adds family columns but 0013 fails) leaves
    # ``events`` present while later-added tables (usage, memories,
    # journal_entries) are missing. Probing only ``events`` would keep the
    # app on PostgresStore against a half-schema, 500ing on queries that
    # reference the missing tables — with NO in-memory fallback. If any
    # critical table is absent, treat the migration as not-applied and fall
    # back so the process still serves requests.
    if settings.use_db and hasattr(store, "table_exists"):
        critical_tables = ("events", "usage", "memories", "journal_entries")
        missing = [t for t in critical_tables if not await store.table_exists(t)]
        if missing:
            logger.error(
                "COMPANION_USE_DB=1 but alembic migrations did not fully apply — "
                "missing table(s): %s. Falling back to the in-memory store for "
                "this process (memory will NOT persist across restarts). A "
                "partial schema would 500 on queries against the missing tables "
                "with no fallback. Run `alembic upgrade head` in the api "
                "container to fix.",
                ", ".join(missing),
            )
            store = InMemoryStore()
    app.state.store = store

    # Auth store + active backend. Same Postgres-vs-in-memory trade-off: if the
    # auth tables are missing, fall back to in-memory auth (process-local
    # sessions) and rebuild the backend against the new store.
    auth_store = make_auth_store(settings)
    if settings.use_db and hasattr(auth_store, "table_exists"):
        if not await auth_store.table_exists():
            logger.error(
                "COMPANION_USE_DB=1 but the users/sessions tables are missing — "
                "falling back to in-memory auth for this process (sessions will "
                "NOT persist across restarts). Run `alembic upgrade head`."
            )
            auth_store = InMemoryAuthStore()
    app.state.auth_store = auth_store
    app.state.auth_backend = build_backend(settings, auth_store)

    # Family store (multi-member families, invites, family providers, family
    # vault metadata). Same Postgres-vs-in-memory axis; falls back to in-memory
    # so the API still boots when the family tables haven't been migrated yet.
    # Postgres impl is complete (mirrors InMemoryFamilyStore line-by-line), so
    # the fallback below only fires when migrations truly haven't run — never
    # on a partially-applied schema or a stubbed method.
    family_store = make_family_store(settings)
    if settings.use_db and hasattr(family_store, "table_exists"):
        if not await family_store.table_exists():
            logger.error(
                "COMPANION_USE_DB=1 but the family tables are missing — "
                "falling back to in-memory family store for this process. "
                "Run `alembic upgrade head` to fix."
            )
            family_store = InMemoryFamilyStore()
    app.state.family_store = family_store

    # Billing store (subscription purchase; hosted-only). Same Postgres-vs-
    # in-memory axis; falls back to in-memory so the API still boots when the
    # billing tables haven't been migrated yet. The in-memory store seeds the
    # plan catalogue from SEED_PLANS so checkout/portal work zero-config.
    billing_store = make_billing_store(settings)
    if settings.use_db and hasattr(billing_store, "table_exists"):
        if not await billing_store.table_exists():
            logger.error(
                "COMPANION_USE_DB=1 but the billing tables are missing — "
                "falling back to in-memory billing store for this process. "
                "Run `alembic upgrade head` to fix."
            )
            billing_store = InMemoryBillingStore()
    app.state.billing_store = billing_store

    # External messengers (Telegram first). The bot token + bound BYOK blob
    # are envelope-encrypted at rest (``MESSENGER_TOKEN_DEK``); ``make_envelope``
    # raises in hosted mode without a key, warns + goes ephemeral self-hosted.
    # Long-poll is one asyncio task per active bot, started below for already-
    # active rows and started on-demand by the bind/PATCH endpoints.
    messenger_store = make_messenger_store(settings)
    if settings.use_db and hasattr(messenger_store, "table_exists"):
        if not await messenger_store.table_exists():
            logger.error(
                "COMPANION_USE_DB=1 but the messengers table is missing — "
                "falling back to in-memory messenger store for this process "
                "(connected bots will NOT persist across restarts). "
                "Run `alembic upgrade head` to fix."
            )
            messenger_store = InMemoryMessengerStore()
    app.state.messenger_store = messenger_store
    envelope = make_envelope(settings)
    app.state.envelope = envelope
    adapter_registry = build_adapter_registry()
    app.state.adapter_registry = adapter_registry
    # PollerDeps is the shared bundle every poller reads from; the router
    # reuses it to spawn/stop pollers on bind / PATCH / DELETE.
    poller_deps = PollerDeps(
        settings=settings,
        store=store,
        ecdh=app.state.ecdh,
        envelope=envelope,
        messenger_store=messenger_store,
        adapter=adapter_registry["telegram"],
        public_origin=settings.public_origin,
    )
    app.state.messenger_deps = poller_deps
    app.state.messenger_pollers: dict[str, MessengerPoller] = {}
    # Start pollers for bots already active before this boot. Best-effort: a
    # transient failure here just logs (the row keeps status=active; the
    # poller can be re-triggered via PATCH status=active from the UI).
    if settings.messenger_long_poll_enabled:
        try:
            for rec in await messenger_store.list_active():
                poller = MessengerPoller(poller_deps, rec)
                await poller.start()
                app.state.messenger_pollers[rec.id] = poller
        except Exception as exc:  # noqa: BLE001 — never let poller startup block boot
            logger.warning("messenger poller startup failed: %s: %s", type(exc).__name__, exc)

    # Scrub any ``sk-...`` token from every log record before it reaches a sink.
    install_redaction()
    yield

    # Stop every running poller so the process exits cleanly (each getUpdates
    # long-poll is an in-flight request that would otherwise be cancelled mid-
    # flight on shutdown, surfacing a noisy traceback).
    for poller in list(app.state.messenger_pollers.values()):
        try:
            await poller.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("messenger poller stop failed: %s: %s", type(exc).__name__, exc)


def create_app() -> FastAPI:
    settings = Settings()
    # Fail fast on a misconfigured deployment (bad mode→backend combo or a
    # missing backend prerequisite) — never serve requests under broken auth.
    validate_auth_config(settings)

    app = FastAPI(
        title="Stillside API",
        version="0.5.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # AuthError → redacted JSON (never carries key material). Auth-internal
    # failures surface as a clean status + detail.
    def _auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.add_exception_handler(AuthError, _auth_error_handler)

    # Also surface boot misconfigurations loudly if they reach request handling
    # (they normally raise at startup; this is a backstop).
    def _auth_config_error_handler(_: Request, exc: AuthConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=500)

    app.add_exception_handler(AuthConfigError, _auth_config_error_handler)

    # Family-store errors carry their own status_code (404 for cross-family,
    # 409 for duplicate, etc.) — the router raises them; we map to JSON here.
    def _family_store_error_handler(_: Request, exc: FamilyStoreError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.add_exception_handler(FamilyStoreError, _family_store_error_handler)

    # I15: rate limiting. The limiter instance lives on app.state so the
    # per-route ``@limiter.limit`` decorators can find it; the 429 handler maps
    # RateLimitExceeded to a clean JSON response; SlowAPIMiddleware wires the
    # decorator-based checks into the ASGI stack. ``limiter.enabled`` is the
    # master switch (off in the test suite, which fires many requests from one
    # IP); flipping it on the singleton affects every decorated route.
    app.state.limiter = limiter
    limiter.enabled = settings.ratelimit_enabled
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware order: AuthMiddleware added BEFORE CORS so CORS ends up
    # outermost (add_middleware inserts at front) — preflight OPTIONS is then
    # handled by CORS before auth could 401 it. SlowAPIMiddleware is added
    # FIRST so it ends up innermost (just before the route) — AuthMiddleware has
    # already set ``request.state.principal`` by then, so per-user keying works.
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/v1")
    app.include_router(auth_router.router, prefix="/v1")
    app.include_router(providers.router, prefix="/v1")
    app.include_router(memory.router, prefix="/v1")
    app.include_router(journal.router, prefix="/v1")
    app.include_router(routing.router, prefix="/v1")
    app.include_router(llm.router, prefix="/v1")
    app.include_router(family.router, prefix="/v1")
    app.include_router(billing.router, prefix="/v1")
    app.include_router(messengers.router, prefix="/v1")
    return app


app = create_app()
