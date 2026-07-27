"""``/v1/auth/*`` + ``/v1/config`` — login flows and the public deployment descriptor.

The router branches on the active backend's ``name``; endpoints that don't apply
to the configured backend return 404 (e.g. ``/signup`` under OIDC, ``/begin`` under
local). All session-establishing endpoints set the HttpOnly + Secure + SameSite=Lax
session cookie via ``sessions.set_session_cookie``; logout revokes the session row
and clears it.

None of these endpoints accept the vault passphrase — auth identity and key custody
are separate concerns (see ``tests/test_vault_auth_separation.py``).
"""

from __future__ import annotations

from ai_companion_contracts import (
    LocalLoginRequest,
    LocalSignupRequest,
    MagicLinkRequest,
    Principal,
    SessionInfo,
)
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import Settings
from ..ratelimit import limiter, user_or_ip_key
from .backends import AuthError
from .backends.local import LocalAccountsBackend
from .backends.magic_link import MagicLinkBackend
from .backends.oidc import OIDCBackend
from .bootstrap import build_auth_config
from .principal import principal_from_user
from .sessions import (
    clear_session_cookie,
    cookie_secure,
    open_sealed,
    seal,
    set_session_cookie,
)

router = APIRouter(tags=["auth"])

_OIDC_STATE_COOKIE = "stillside_oidc_state"
_OIDC_STATE_TTL = 5 * 60


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _backend(request: Request):
    return request.app.state.auth_backend


def _require_backend(request: Request, name: str):
    b = _backend(request)
    if b.name != name:
        raise HTTPException(status_code=404, detail=f"{name} auth is not enabled")
    return b


# --- public deployment descriptor ---


@router.get("/config")
async def get_config(request: Request) -> JSONResponse:
    cfg = build_auth_config(_settings(request))
    return JSONResponse(cfg.model_dump(mode="json"))


# --- local accounts ---


@router.post("/auth/signup")
@limiter.limit("10/minute")
async def signup(body: LocalSignupRequest, request: Request) -> JSONResponse:
    backend = _require_backend(request, "local")
    assert isinstance(backend, LocalAccountsBackend)
    try:
        user, token = await backend.signup(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    resp = JSONResponse(principal_from_user(user, backend.name).model_dump(mode="json"))
    set_session_cookie(resp, token, _settings(request))
    return resp


@router.post("/auth/login")
@limiter.limit("10/minute")
async def login(body: LocalLoginRequest, request: Request) -> JSONResponse:
    backend = _require_backend(request, "local")
    assert isinstance(backend, LocalAccountsBackend)
    try:
        user, token = await backend.login(
            email=body.email,
            password=body.password,
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    resp = JSONResponse(principal_from_user(user, backend.name).model_dump(mode="json"))
    set_session_cookie(resp, token, _settings(request))
    return resp


# --- OIDC ---


@router.get("/auth/begin")
async def oidc_begin(request: Request, next: str | None = Query(default=None)) -> RedirectResponse:
    backend = _require_backend(request, "oidc")
    assert isinstance(backend, OIDCBackend)
    await backend.discovery()
    url, state, verifier = backend.begin_login()
    resp = RedirectResponse(url, status_code=303)
    # Seal {state, verifier} into a short-lived cookie so callback is stateless.
    sealed = seal({"state": state, "verifier": verifier}, _settings(request).auth_state_secret)
    resp.set_cookie(
        key=_OIDC_STATE_COOKIE,
        value=sealed,
        max_age=_OIDC_STATE_TTL,
        httponly=True,
        secure=cookie_secure(_settings(request)),
        samesite="lax",
        path="/",
    )
    return resp


@router.get("/auth/callback")
async def oidc_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    backend = _require_backend(request, "oidc")
    assert isinstance(backend, OIDCBackend)
    settings = _settings(request)
    home = settings.public_origin.rstrip("/") + "/"
    if error or not code or not state:
        # IdP error / malformed callback — send the user home unauthenticated.
        resp = RedirectResponse(home, status_code=303)
        resp.delete_cookie(_OIDC_STATE_COOKIE, path="/")
        return resp
    sealed = request.cookies.get(_OIDC_STATE_COOKIE)
    payload = open_sealed(sealed, settings.auth_state_secret) if sealed else None
    if payload is None or payload.get("state") != state:
        raise HTTPException(status_code=400, detail="OIDC state mismatch — please sign in again.")
    try:
        user, token = await backend.handle_callback(
            code=code,
            state=state,
            verifier=str(payload.get("verifier", "")),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    resp = RedirectResponse(home, status_code=303)
    set_session_cookie(resp, token, settings)
    resp.delete_cookie(_OIDC_STATE_COOKIE, path="/")
    return resp


# --- magic link ---


@router.post("/auth/magiclink")
@limiter.limit("10/minute")
async def magiclink_send(body: MagicLinkRequest, request: Request) -> JSONResponse:
    backend = _require_backend(request, "magic_link")
    assert isinstance(backend, MagicLinkBackend)
    try:
        await backend.send(email=body.email)
    except AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    # Don't reveal whether the email has an account; always ack.
    return JSONResponse({"ok": True})


@router.get("/auth/magiclink/verify")
@limiter.limit("30/minute")
async def magiclink_verify(request: Request, token: str = Query(...)) -> RedirectResponse:
    backend = _require_backend(request, "magic_link")
    assert isinstance(backend, MagicLinkBackend)
    settings = _settings(request)
    home = settings.public_origin.rstrip("/") + "/"
    try:
        user, session_token = await backend.verify(
            token, user_agent=request.headers.get("user-agent")
        )
    except AuthError:
        # Bad/expired token — send home unauthenticated. (A JSON 400 would be
        # friendlier for an SPA, but magic links are opened from email by the
        # browser directly, so a redirect is the robust choice.)
        return RedirectResponse(home, status_code=303)
    # Auto-attach to a family if a pending invite exists for this email. The
    # accept endpoint is the canonical flow; this is the convenience path for
    # users who land on a magiclink first (e.g. invited before they had an
    # account). Idempotent on already-attached. No-op when there's no invite.
    try:
        from ..family.attach import maybe_attach_user_by_email

        await maybe_attach_user_by_email(
            request.app.state.auth_store, request.app.state.family_store, user
        )
    except Exception:  # noqa: BLE001 — attach is a best-effort bonus
        pass
    resp = RedirectResponse(home, status_code=303)
    set_session_cookie(resp, session_token, settings)
    return resp


# --- shared ---


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    settings = _settings(request)
    token = request.cookies.get(settings.auth_session_cookie)
    if token:
        await request.app.state.auth_store.revoke_session(token)
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp, settings)
    return resp


@router.get("/auth/me")
async def me(request: Request) -> JSONResponse:
    # Not in PUBLIC_PATHS → middleware guarantees a Principal is present.
    principal = request.state.principal
    if principal is None:  # pragma: no cover — middleware 401s first
        raise HTTPException(status_code=401, detail="Not authenticated")
    return JSONResponse(principal.model_dump(mode="json"))


# --- session management (M2) ---
#
# Active-device list + revoke. The session ``token`` is the cookie value (a
# secret) and is NEVER surfaced — only the opaque surrogate ``id`` (from the
# ``sessions.id`` column, migration 0016) keys the revoke endpoints. All three
# are behind the auth middleware (not PUBLIC_PATHS) so a Principal is present;
# each scopes strictly to ``principal.user_id`` and a cross-user revoke is a
# 404 (not 403), per the project's cross-tenant convention.


def _require_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover — middleware 401s first
        raise HTTPException(status_code=401, detail="Not authenticated")
    return principal


def _current_token(request: Request) -> str | None:
    return request.cookies.get(_settings(request).auth_session_cookie)


@router.get("/auth/sessions", response_model=list[SessionInfo])
@limiter.limit("30/minute", key_func=user_or_ip_key)
async def list_sessions(request: Request) -> list[SessionInfo]:
    """The caller's active (non-revoked) sessions, newest first. The session
    matching the request cookie is marked ``current: true`` and cannot be
    revoked from its own card (use ``DELETE /v1/auth/sessions/{id}`` for the
    others, or ``POST /v1/auth/logout`` for the current one)."""
    principal = _require_principal(request)
    store = request.app.state.auth_store
    current = _current_token(request)
    rows = await store.list_sessions(user_id=principal.user_id)
    return [
        SessionInfo(
            id=r.id or "",
            created_at=r.created_at or r.expires_at,
            expires_at=r.expires_at,
            user_agent=r.user_agent,
            current=(current is not None and r.token == current),
        )
        for r in rows
    ]


@router.delete("/auth/sessions/{session_id}", status_code=204)
async def revoke_one_session(session_id: str, request: Request) -> None:
    """Revoke one of the caller's sessions by surrogate id. Revoking the
    *current* session is rejected with 409 (use ``POST /v1/auth/logout`` so the
    cookie is also cleared). A no-match (wrong id, another user's session, or
    already revoked) is a 404 — never reveals whether the id belonged to
    someone else."""
    principal = _require_principal(request)
    store = request.app.state.auth_store
    current = _current_token(request)
    # Refuse to revoke the current session here — the cookie would remain valid
    # client-side while the row is dead, a confusing half-state. logout clears
    # both. Detect by looking up the row's token via list_sessions.
    if current is not None:
        rows = await store.list_sessions(user_id=principal.user_id)
        for r in rows:
            if r.id == session_id and r.token == current:
                raise HTTPException(status_code=409, detail="use logout to end the current session")
    ok = await store.revoke_session_by_id(user_id=principal.user_id, session_id=session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")


@router.delete("/auth/sessions")
async def revoke_other_sessions(request: Request) -> JSONResponse:
    """Sign out everywhere EXCEPT the current session ("revoke all other
    sessions"). Returns ``{"revoked": n}``. Idempotent — re-running with only
    the current session left revokes nothing and returns 0."""
    principal = _require_principal(request)
    store = request.app.state.auth_store
    current = _current_token(request)
    n = await store.revoke_all_sessions(user_id=principal.user_id, keep_token=current)
    return JSONResponse({"revoked": n})


__all__ = ["router"]
