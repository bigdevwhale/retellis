"""cleanup — orphaned ``family_members`` rows + empty families

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-13 00:00:00

One-time DATA migration (no schema change). Repairs the "one family per
user" invariant at the data layer so the existing rows agree with the
application-level pointer ``users.family_id``.

Background: ``FamilyStore.get_family_for_user`` historically did a
``SELECT ... LIMIT 1`` over ``family_members`` with no ``ORDER BY``. When a
user accumulated several ``family_members`` rows (older memberships that
were never cleaned up — the previous ``disband_family`` order-of-operations
wiped ``family_members`` BEFORE iterating members to clear
``users.family_id``, so the principal pointer drifted out of sync with the
membership rows), the lookup returned an arbitrary family that did not
match ``users.family_id``. The LLM stream endpoint compares
``body.family_id`` against ``principal.family_id`` and 404s on a mismatch —
so a family chat turn failed with a confusing "Could not reach the
companion API" error.

Two code fixes ship alongside this migration:

1. ``get_family_for_user`` now accepts ``preferred_family_id`` (the
   principal's ``users.family_id``) and returns the matching family when
   the user is a member of it; the fallback is ordered by
   ``joined_at DESC`` (deterministic).
2. ``disband_family`` snapshots members BEFORE wiping the rows so
   ``users.family_id`` is reliably cleared for every former member.

This migration repairs the EXISTING data so the new code paths start from
a consistent state. It is idempotent — re-running it is a no-op once the
orphans are gone. The downgrade is intentionally a no-op: the deleted rows
are dead data with no recovery value, and "downgrading" would resurrect
inconsistent state.

The cleanup:

  Step 1 — drop membership rows whose family is NOT the user's current
  ``users.family_id``. For users whose ``users.family_id`` is NULL but who
  still have ``family_members`` rows, ALL their memberships are orphaned
  (the pointer says "not in a family"); drop them all.

  Step 1.5 — null ``users.family_id`` / ``family_role`` for any user whose
  pointer no longer has a matching membership row. This repairs the
  "stale pointer" case: a user whose ``users.family_id`` pointed at a
  family they were never (or no longer) a member of. Without this step,
  that user would be stuck — ``GET /v1/family`` 404s (no membership for
  the preferred family) AND ``POST /v1/family`` 409s (the
  ``principal.family_id is not None`` guard). There is no LEGITIMATE
  state where ``users.family_id`` is set but the user has no membership
  row in that family; nulling the pointer is the correct repair and lets
  the user create/join a family fresh.

  Step 2 — disband families that now have zero members (no one is left
  to own them). Cascade: drop their ``family_providers`` and
  ``family_invites`` first, then the ``families`` row — mirroring
  ``PostgresFamilyStore.disband_family``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: drop orphaned membership rows.
    #
    # A membership is "orphaned" when its family_id is not the user's
    # current ``users.family_id``. LEFT JOIN users so users with NULL
    # family_id are handled: for them, EVERY membership is orphaned
    # (the join condition ``family_members.family_id = users.family_id``
    # is never true when users.family_id IS NULL), so the WHERE clause
    # drops all their memberships.
    op.execute(
        sa.text(
            """
            DELETE FROM family_members fm
            USING users u
            WHERE fm.user_id = u.id
              AND fm.family_id IS DISTINCT FROM u.family_id
            """
        )
    )

    # Step 1.5: null stale ``users.family_id`` pointers.
    #
    # After step 1, a user's remaining memberships are either:
    #   - exactly the one matching ``users.family_id`` (the happy path), or
    #   - nothing, if ``users.family_id`` pointed at a family the user was
    #     never (or no longer) a member of (the orphaned membership for
    #     THAT family was also deleted in step 1), or
    #   - nothing, if ``users.family_id`` was already NULL.
    # In the second case the pointer is stale and traps the user (404 on
    # reads, 409 on create). Null it so the user can start fresh. There
    # is no legitimate "pointer set, no membership" state — this is a
    # repair, not a detach.
    op.execute(
        sa.text(
            """
            UPDATE users u
            SET family_id = NULL, family_role = NULL
            WHERE u.family_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM family_members fm
                WHERE fm.user_id = u.id AND fm.family_id = u.family_id
              )
            """
        )
    )

    # Step 2: disband families with zero members.
    #
    # Collect the family_ids that have no remaining members, then cascade
    # the delete through family_providers and family_invites (the FK-less
    # dependents — see migration 0010/0012), then drop the family row.
    # Mirror PostgresFamilyStore.disband_family.
    op.execute(
        sa.text(
            """
            DELETE FROM family_providers
            WHERE family_id IN (
                SELECT f.id FROM families f
                WHERE NOT EXISTS (
                    SELECT 1 FROM family_members fm WHERE fm.family_id = f.id
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM family_invites
            WHERE family_id IN (
                SELECT f.id FROM families f
                WHERE NOT EXISTS (
                    SELECT 1 FROM family_members fm WHERE fm.family_id = f.id
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM families f
            WHERE NOT EXISTS (
                SELECT 1 FROM family_members fm WHERE fm.family_id = f.id
            )
            """
        )
    )


def downgrade() -> None:
    # No-op: the deleted rows are dead data with no recovery value.
    # Resurrecting orphaned memberships would re-introduce the
    # inconsistency this migration repaired.
    pass
