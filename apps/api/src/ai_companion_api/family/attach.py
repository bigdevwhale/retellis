"""Auto-attach helpers — used by the magiclink verify flow to opportunistically
attach a freshly-authed user to a family they were invited to by email.

The canonical flow is ``POST /v1/family/accept`` (token in body) which is
explicit and works for users who already have an account. This is a
convenience for users who land on the magiclink email first (e.g. they were
invited before they had an account, or their invite was the only email
they got). It's idempotent: a no-op when there's no pending invite for the
email, or when the user is already in a family, or when the user is already
attached to this invite's family.

The attach succeeds only when:
- the user has no current family (one family per user, see PLAN §Family); and
- the user isn't already in the invite's family.
"""

from __future__ import annotations

import logging

from ..auth.store import UserRecord
from .store import FamilyRole, FamilyStore, FamilyStoreError

logger = logging.getLogger(__name__)


async def maybe_attach_user_by_email(
    auth_store,  # type: ignore[no-untyped-def]  # AuthStore
    family_store: FamilyStore,
    user: UserRecord,
) -> bool:
    """Idempotently attach ``user`` to a family they've been invited to by email.

    Returns True if the user was attached, False otherwise. Never raises — a
    failed auto-attach is a bonus, not a blocker (the user can still accept
    the invite from the family settings page once they have a session).
    """
    if user.email is None:
        return False
    if user.family_id is not None:
        return False
    try:
        invite = await family_store.consume_pending_invite_for_email(email=user.email)
    except Exception:  # noqa: BLE001 — best-effort bonus
        logger.exception("family auto-attach: failed to look up invite for %s", user.email)
        return False
    if invite is None:
        return False
    if invite.accepted_at is not None or invite.expires_at <= invite.created_at:
        return False
    try:
        await family_store.add_member(
            family_id=invite.family_id,
            user_id=user.id,
            family_role=FamilyRole(invite.role),
            family_display_name=user.display_name or user.email.split("@")[0],
            relation="other",
            color="#7c3aed",
        )
    except FamilyStoreError:
        return False
    try:
        await family_store.mark_invite_accepted(invite_id=invite.id)
    except Exception:  # noqa: BLE001
        pass
    try:
        await auth_store.set_user_family(
            user_id=user.id, family_id=invite.family_id, family_role=invite.role
        )
    except Exception:  # noqa: BLE001
        pass
    return True


__all__ = ["maybe_attach_user_by_email"]
