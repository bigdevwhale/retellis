"""Principal helpers — build the verified identity from a persisted user row."""

from __future__ import annotations

from ai_companion_contracts import Principal

from .store import UserRecord


def principal_from_user(user: UserRecord, backend: str) -> Principal:
    """Build the wire ``Principal`` from a stored user row + the backend name.

    The Principal is the source of the ``user_id`` partition key scoped through
    every store query. It carries no key material — auth identity is decoupled
    from the BYOK vault (the passphrase never enters this path)."""
    return Principal(
        user_id=user.id,
        subject=user.subject,
        issuer=user.issuer,
        email=user.email,
        display_name=user.display_name,
        plan=user.plan,
        credits_usd=user.credits_usd,
        auth_backend=backend,
        family_id=user.family_id,
        family_role=user.family_role,  # type: ignore[arg-type]
    )


__all__ = ["principal_from_user"]
