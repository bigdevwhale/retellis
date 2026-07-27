"""FastAPI dependencies.

Identity is now a verified ``Principal`` resolved by ``auth.middleware.AuthMiddleware``
and attached to ``request.state.principal`` — replacing the single-user ``X-User-Id``
self-assertion. The legacy ``X-User-Id`` header is honored only behind
``AUTH_ALLOW_INSECURE_USER_HEADER=1`` (tests / local dev; never in production), and
the middleware synthesizes a Principal from it so the escape hatch is end-to-end.
"""

from __future__ import annotations

from ai_companion_contracts import Principal
from fastapi import HTTPException, Request

from .auth.principal import principal_from_user
from .billing.store import BillingStore
from .config import Settings
from .memory.store import MemoryStore
from .vault.session_ecdh import SessionECDH


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_session_ecdh(request: Request) -> SessionECDH:
    return request.app.state.ecdh  # type: ignore[no-any-return]


def get_store(request: Request) -> MemoryStore:
    return request.app.state.store  # type: ignore[no-any-return]


def get_billing_store(request: Request) -> BillingStore:
    return request.app.state.billing_store  # type: ignore[no-any-return]


async def get_current_principal(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is not None:
        return principal
    # Defensive fallback for the dev/test escape hatch (the middleware already
    # covers this; kept here so dependencies work even outside the middleware).
    # M1.2: a missing X-User-Id header no longer impersonates
    # ``settings.default_user_id`` — it 401s, matching multi-user semantics.
    settings: Settings = request.app.state.settings
    if getattr(settings, "auth_allow_insecure_user_header", False):
        hid = request.headers.get("X-User-Id")
        if hid:
            user = await request.app.state.auth_store.get_user(hid)
            if user is not None:
                return principal_from_user(user, "insecure")
            return Principal(
                user_id=hid,
                subject=hid,
                issuer="insecure-header",
                auth_backend="insecure",
            )
    raise HTTPException(status_code=401, detail="Not authenticated")


async def get_current_user_id(request: Request) -> str:
    return (await get_current_principal(request)).user_id
