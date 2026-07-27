"""Auth middleware — resolves the verified Principal for every request.

For session backends (local / oidc / magic_link) it reads the opaque session
cookie → ``AuthStore.get_session`` → ``get_user`` → ``Principal``. For the
trusted-header backend it calls ``backend.resolve(request)`` on every request
(the proxy authenticates each request; there is no session).

The resolved Principal (or None) is attached to ``request.state.principal`` so
``deps.get_current_principal`` can read it without re-doing the work. Requests to
non-public paths without a Principal get 401. OPTIONS (CORS preflight) always
passes — CORS middleware is outermost and handles preflight, this is belt-and-
suspenders.
"""

from __future__ import annotations

from ai_companion_contracts import Principal
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..config import Settings
from .principal import principal_from_user
from .store import AuthStore

# Paths that may be hit without a Principal, scoped by HTTP method (I16).
# ``PUBLIC_PATHS`` (the union) is kept for introspection, but the actual gate
# uses ``_is_public(method, path)`` so an unauthed POST to a read-only
# descriptor (e.g. ``POST /v1/health``) is rejected — the old method-blind
# ``path in PUBLIC_PATHS`` check let any method through on these paths.

# Read-only public descriptors + GET auth-flow redirects — GET/HEAD only.
# ``/v1/auth/begin`` and ``/v1/auth/callback`` are GET because the OIDC IdP
# redirects the browser to them with ``?code=&state=``; ``magiclink/verify`` is
# a GET click-through from the email link. They start a flow / consume a token
# but do so via GET, so they ride the GET public gate.
_PUBLIC_GET: frozenset[str] = frozenset(
    {
        "/v1/health",
        "/v1/config",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/docs/oauth2-redirect",
        "/v1/auth/begin",
        "/v1/auth/callback",
        "/v1/auth/magiclink/verify",
        # Billing plan catalogue — public so an anonymous visitor can see
        # prices/credits before signing in. Checkout/portal/subscription still
        # require a Principal; the webhooks are public POST (signature-gated).
        "/v1/billing/plans",
        # Family invite landing — the GET page must render unauthed so an
        # invitee lands on login/signup with the sealed token in the URL. The
        # POST /v1/family/accept still requires a Principal (authed redemption).
        "/v1/family/accept",
    }
)

# Unauthed auth-flow mutations — POST only.
_PUBLIC_POST: frozenset[str] = frozenset(
    {
        "/v1/auth/signup",
        "/v1/auth/login",
        "/v1/auth/magiclink",
        "/v1/auth/logout",
        # Billing provider webhooks — unauthenticated here (signature verified
        # inside the handler, the ONLY auth on these routes). Paddle posts an
        # HMAC-signed body; ЮKassa posts a notification we re-verify by fetching
        # the payment from their API; Prodamus posts an HMAC-signed JSON body
        # (Sign over `submit`). Idempotency guard in the billing store dedups
        # redeliveries.
        "/v1/billing/webhook/paddle",
        "/v1/billing/webhook/yookassa",
        "/v1/billing/webhook/prodamus",
    }
)

PUBLIC_PATHS: frozenset[str] = _PUBLIC_GET | _PUBLIC_POST


def _is_public(method: str, path: str) -> bool:
    """Method-scoped public-path gate. ``GET``/``HEAD`` → descriptors; ``POST``
    → auth-flow mutations. Every other method on a public path still requires
    a Principal (so ``DELETE /v1/auth/login`` is not accidentally allowed)."""
    m = method.upper()
    if m in ("GET", "HEAD") and path in _PUBLIC_GET:
        return True
    if m == "POST" and path in _PUBLIC_POST:
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.settings = settings
        super().__init__(app)

    async def _resolve_principal(self, request: Request):
        store: AuthStore = request.app.state.auth_store
        backend = request.app.state.auth_backend
        if backend.name == "trusted_header":
            return await backend.resolve(request)
        token = request.cookies.get(self.settings.auth_session_cookie)
        if not token:
            return None
        session = await store.get_session(token)
        if session is None:
            return None
        user = await store.get_user(session.user_id)
        if user is None:
            return None
        return principal_from_user(user, backend.name)

    async def _insecure_header_principal(self, request: Request):
        # Dev/test escape hatch: honor an EXPLICIT ``X-User-Id`` header as an
        # insecure Principal. NEVER enable in production — it bypasses the
        # verified identity (and ``auth.bootstrap`` hard-fails the boot in
        # hosted mode if the flag is on). Sprint 6 M1.2: a missing header no
        # longer silently impersonates ``settings.default_user_id`` — it
        # returns None so the dispatch path 401s, matching multi-user
        # semantics. Loads the user record (when present) so family_id /
        # family_role / email are populated; a missing user falls back to a
        # bare Principal (the "user does not exist" path, which still 404s on
        # family-scoped reads).
        hid = request.headers.get("X-User-Id")
        if not hid:
            return None
        user = await request.app.state.auth_store.get_user(hid)
        if user is not None:
            return principal_from_user(user, "insecure")
        return Principal(
            user_id=hid,
            subject=hid,
            issuer="insecure-header",
            auth_backend="insecure",
        )

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        # Preflight / public paths: don't require a Principal (but still resolve
        # one if present, so /v1/auth/me-style "am I logged in" checks could work
        # on public routes too).
        try:
            principal = await self._resolve_principal(request)
        except Exception:  # noqa: BLE001 — never let auth resolution crash a request
            principal = None

        # Dev/test escape hatch: honor an explicit X-User-Id header as an
        # insecure Principal. NEVER enable in production — it bypasses the
        # verified identity. A missing header returns None (M1.2: no implicit
        # default-user impersonation) so the 401 gate below applies.
        if principal is None and self.settings.auth_allow_insecure_user_header:
            principal = await self._insecure_header_principal(request)
        request.state.principal = principal

        if request.method == "OPTIONS":
            return await call_next(request)

        if _is_public(request.method, request.url.path):
            return await call_next(request)

        if principal is None:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)


__all__ = ["AuthMiddleware", "PUBLIC_PATHS", "_is_public"]
