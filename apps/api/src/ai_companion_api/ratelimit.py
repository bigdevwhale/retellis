"""Rate limiting (I15) — slowapi-based guards on abuse-prone endpoints.

Guards:
  * brute-force on ``/v1/auth/login`` / ``/signup`` / ``/auth/magiclink`` — per-IP.
  * invite spam on ``POST /v1/family/invites`` — per-owner (per-user).
  * runaway cost on ``POST /v1/llm/stream`` — per-user (the cost-critical axis
    in hosted multi-user) plus a per-IP burst cap.

Per-user keying reads ``request.state.principal`` (set by AuthMiddleware before
the route's limiter check runs), falling back to the remote address when there
is no principal. Auth endpoints are pre-auth, so they key purely by IP.

Honest limit: the default storage is in-memory (``memory://``), so under
multiple uvicorn workers each worker keeps its own counters and the effective
limit is N×the configured rate. Set ``RATELIMIT_STORAGE_URI`` to a redis URL to
share counters across workers. See ``Settings.ratelimit_storage_uri``.

The limiter is constructed at import time (route decorators capture it when the
router modules import), so the storage URI is read from the environment here,
not from ``Settings`` — but it is the same ``RATELIMIT_STORAGE_URI`` env var
``Settings.ratelimit_storage_uri`` reads, so they stay consistent.
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def user_or_ip_key(request: Request) -> str:
    """Per-user when authenticated (``request.state.principal`` is set by
    AuthMiddleware), else per-IP. Used on authed cost-spending endpoints
    (``/llm/stream``, ``/family/invites``) so one user's quota is independent of
    their network and of other users sharing an IP."""
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return f"user:{principal.user_id}"
    return f"ip:{get_remote_address(request)}"


# Default key_func is the remote address — the right default for the pre-auth
# auth endpoints (no principal yet). Per-user endpoints override key_func.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)


__all__ = ["limiter", "user_or_ip_key"]
