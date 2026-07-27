"""foreign keys + ON DELETE cascade/SET NULL + sessions surrogate id

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-14 00:00:00

Sprint 6 (I21). The schema so far has had ZERO foreign keys — every cross-table
relationship was app-enforced, which already drifted once (migration 0015
repaired orphaned ``family_members`` rows). This migration makes the cascade
contract explicit at the DB layer so user/persona/family deletion cleans up
their dependents deterministically, and it agrees with the two existing reset
paths:

  - ``DELETE /v1/memory/convo`` deletes ``events`` by
    ``(user_id, persona_id, convo_id)`` — deleting the rows themselves, so no
    CASCADE fires (nothing is deleting a ``users``/``families``/``personas``
    parent row).
  - ``DELETE /v1/memory?persona_id=`` (``wipe_persona_memory``) deletes
    events+memories+outgoing shares by ``(user_id, persona_id)``; the persona
    ROW is not deleted, so the (omitted) ``personas``→events/memories CASCADE
    never fires. See below for why persona_id FKs are omitted entirely.
  - ``disband_family`` (family store) already deletes family_providers +
    family_invites before the families row — with CASCADE that becomes
    redundant but not harmful (explicit order kept as defense-in-depth).

**persona_id FKs are intentionally omitted** on events, memories,
journal_entries, and memory_shares (donor/receiver). Only CUSTOM personas are
rows in ``personas``; the builtins (sam/aria/fam/lou) are not, so an FK
``events.persona_id → personas.id`` plus orphan cleanup would delete the
majority of event rows. User-delete cascade still reaches events/memories via
the ``user_id`` FK (CASCADE), so the cleanup guarantee is intact — only the
persona-scoped shortcut is forgone, which matches how the app already works
(reset paths key off ``user_id`` + ``persona_id`` string equality, not the
``personas`` table).

Cascade policy (Sprint 6 user decisions):
  - **CASCADE** when the child is "owned" by the parent and has no meaning
    without it: providers/personas/events/memories/journal/usage/memory_shares/
    sessions → users; family_members/family_providers/family_invites →
    families; family_members → users; families → owner (deleting the owner
    DISBANDS the family).
  - **SET NULL** for soft pointers: family_id / participant_user_id /
    prev_event_id / memories.superseded_by / journal.source_event_id /
    users.family_id / family_invites.invited_by. The child row survives, just
    detached.

Idempotent: every ALTER is guarded by an information_schema / pg_constraint
existence check so re-running (or a partially-applied previous attempt) is a
no-op. Runs in the surrounding alembic transaction. The pgcrypto extension is
created (IF NOT EXISTS) for ``gen_random_uuid()`` to backfill the new
``sessions.id`` surrogate on existing rows.

**dev-DB warning:** the orphan-cleanup step (Step 1) deletes legacy rows whose
``user_id`` has no matching ``users`` row — e.g. events written under
``default_user_id`` before that user existed. On a prod DB with real accounts
there should be none; on a long-lived dev DB this is destructive. Back up first
if the dev data matters.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (constraint_name, table, column, ref_table, ref_column, ondelete)
# persona_id FKs deliberately absent — see module docstring.
_FKS: list[tuple[str, str, str, str, str, str]] = [
    ("fk_sessions_user", "sessions", "user_id", "users", "id", "CASCADE"),
    ("fk_providers_user", "providers", "user_id", "users", "id", "CASCADE"),
    ("fk_personas_user", "personas", "user_id", "users", "id", "CASCADE"),
    ("fk_events_user", "events", "user_id", "users", "id", "CASCADE"),
    ("fk_events_family", "events", "family_id", "families", "id", "SET NULL"),
    ("fk_events_participant", "events", "participant_user_id", "users", "id", "SET NULL"),
    ("fk_events_prev", "events", "prev_event_id", "events", "id", "SET NULL"),
    ("fk_memories_user", "memories", "user_id", "users", "id", "CASCADE"),
    ("fk_memories_family", "memories", "family_id", "families", "id", "SET NULL"),
    ("fk_memories_participant", "memories", "participant_user_id", "users", "id", "SET NULL"),
    ("fk_memories_superseded_by", "memories", "superseded_by", "memories", "id", "SET NULL"),
    ("fk_journal_user", "journal_entries", "user_id", "users", "id", "CASCADE"),
    ("fk_journal_family", "journal_entries", "family_id", "families", "id", "SET NULL"),
    ("fk_journal_participant", "journal_entries", "participant_user_id", "users", "id", "SET NULL"),
    ("fk_journal_source_event", "journal_entries", "source_event_id", "events", "id", "SET NULL"),
    ("fk_usage_user", "usage", "user_id", "users", "id", "CASCADE"),
    ("fk_usage_family", "usage", "family_id", "families", "id", "SET NULL"),
    ("fk_memory_shares_user", "memory_shares", "user_id", "users", "id", "CASCADE"),
    ("fk_families_owner", "families", "owner_user_id", "users", "id", "CASCADE"),
    ("fk_family_members_family", "family_members", "family_id", "families", "id", "CASCADE"),
    ("fk_family_members_user", "family_members", "user_id", "users", "id", "CASCADE"),
    ("fk_family_providers_family", "family_providers", "family_id", "families", "id", "CASCADE"),
    ("fk_family_invites_family", "family_invites", "family_id", "families", "id", "CASCADE"),
    ("fk_family_invites_invited_by", "family_invites", "invited_by", "users", "id", "SET NULL"),
    ("fk_users_family", "users", "family_id", "families", "id", "SET NULL"),
]

# Tables with a CASCADE-to-users ``user_id`` column that must be orphan-free
# before the FK constraint can be added.
_USER_CHILDREN = (
    "events",
    "memories",
    "journal_entries",
    "usage",
    "memory_shares",
    "providers",
    "personas",
    "sessions",
)
# Tables with a SET-NULL ``family_id`` pointer (NULLed, not deleted, when
# the families row is gone).
_FAMILY_PTR_TABLES = ("events", "memories", "journal_entries", "usage")
# Tables with a SET-NULL ``participant_user_id`` pointer.
_PARTICIPANT_TABLES = ("events", "memories", "journal_entries")


def upgrade() -> None:
    # pgcrypto for gen_random_uuid() — backfills sessions.id on existing rows.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --- Step 1: orphan cleanup -------------------------------------------
    # Every ADD CONSTRAINT below would fail on a DB with dangling rows, so wipe
    # / null them first. All statements are idempotent (re-running is a no-op
    # once the orphans are gone). persona_id is NOT touched (no persona FK).

    # CASCADE children: delete rows whose user_id has no matching user.
    for table in _USER_CHILDREN:
        op.execute(
            sa.text(
                f"""
                DELETE FROM {table}
                WHERE user_id NOT IN (SELECT id FROM users)
                """
            )
        )

    # family_members: orphan on either side.
    op.execute(
        sa.text(
            """
            DELETE FROM family_members
            WHERE family_id NOT IN (SELECT id FROM families)
               OR user_id NOT IN (SELECT id FROM users)
            """
        )
    )

    # family_providers / family_invites: orphan family_id.
    for table in ("family_providers", "family_invites"):
        op.execute(
            sa.text(
                f"""
                DELETE FROM {table}
                WHERE family_id NOT IN (SELECT id FROM families)
                """
            )
        )

    # families whose OWNER is gone: cascade-delete their dependents, then the
    # family row (mirrors disband_family order; the fk_families_owner CASCADE
    # will handle this going forward, but existing orphans must be cleared
    # before that constraint can be added).
    op.execute(
        sa.text(
            """
            DELETE FROM family_providers
            WHERE family_id IN (
                SELECT id FROM families
                WHERE owner_user_id NOT IN (SELECT id FROM users)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM family_invites
            WHERE family_id IN (
                SELECT id FROM families
                WHERE owner_user_id NOT IN (SELECT id FROM users)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM family_members
            WHERE family_id IN (
                SELECT id FROM families
                WHERE owner_user_id NOT IN (SELECT id FROM users)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM families
            WHERE owner_user_id NOT IN (SELECT id FROM users)
            """
        )
    )

    # SET NULL pointers: detach dangling family_id / participant_user_id /
    # users.family_id / self-references / journal source_event_id.
    for table in _FAMILY_PTR_TABLES:
        op.execute(
            sa.text(
                f"""
                UPDATE {table} SET family_id = NULL
                WHERE family_id IS NOT NULL
                  AND family_id NOT IN (SELECT id FROM families)
                """
            )
        )
    for table in _PARTICIPANT_TABLES:
        op.execute(
            sa.text(
                f"""
                UPDATE {table} SET participant_user_id = NULL
                WHERE participant_user_id IS NOT NULL
                  AND participant_user_id NOT IN (SELECT id FROM users)
                """
            )
        )
    op.execute(
        sa.text(
            """
            UPDATE users SET family_id = NULL
            WHERE family_id IS NOT NULL
              AND family_id NOT IN (SELECT id FROM families)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE events SET prev_event_id = NULL
            WHERE prev_event_id IS NOT NULL
              AND prev_event_id NOT IN (SELECT id FROM events)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE memories SET superseded_by = NULL
            WHERE superseded_by IS NOT NULL
              AND superseded_by NOT IN (SELECT id FROM memories)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE journal_entries SET source_event_id = NULL
            WHERE source_event_id IS NOT NULL
              AND source_event_id NOT IN (SELECT id FROM events)
            """
        )
    )

    # --- Step 2: sessions surrogate id + user_agent (M2) -----------------
    # The cookie ``token`` is a secret; the session-list / revoke endpoints key
    # off this opaque id instead. Backfill existing rows with gen_random_uuid
    # (32-char hex, matching the width/style of other ids), then enforce NOT
    # NULL + UNIQUE. user_agent is nullable (rows created before this migration
    # and backends that don't capture it).
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sessions' AND column_name = 'id'
                ) THEN
                    ALTER TABLE sessions ADD COLUMN id VARCHAR(64);
                    UPDATE sessions
                       SET id = replace(gen_random_uuid()::text, '-', '')
                       WHERE id IS NULL;
                    ALTER TABLE sessions ALTER COLUMN id SET NOT NULL;
                    ALTER TABLE sessions ADD CONSTRAINT uq_sessions_id UNIQUE (id);
                END IF;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sessions' AND column_name = 'user_agent'
                ) THEN
                    ALTER TABLE sessions ADD COLUMN user_agent TEXT;
                END IF;
            END $$;
            """
        )
    )

    # --- Step 3: family_invites.invited_by must be nullable for SET NULL ---
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'family_invites' AND column_name = 'invited_by'
                      AND is_nullable = 'NO'
                ) THEN
                    ALTER TABLE family_invites ALTER COLUMN invited_by DROP NOT NULL;
                END IF;
            END $$;
            """
        )
    )

    # --- Step 4: ADD CONSTRAINT (idempotent via pg_constraint check) ------
    for name, table, col, ref_table, ref_col, ondelete in _FKS:
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = '{name}'
                    ) THEN
                        ALTER TABLE {table}
                          ADD CONSTRAINT {name}
                          FOREIGN KEY ({col})
                          REFERENCES {ref_table}({ref_col})
                          ON DELETE {ondelete};
                    END IF;
                END $$;
                """
            )
        )


def downgrade() -> None:
    # Drop the FK constraints (IF EXISTS — safe if upgrade partially ran).
    for name, _table, _col, _ref_table, _ref_col, _ondelete in _FKS:
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = '{name}'
                    ) THEN
                        ALTER TABLE {_table} DROP CONSTRAINT {name};
                    END IF;
                END $$;
                """
            )
        )

    # Drop the sessions surrogate columns. ``invited_by`` is left nullable —
    # restoring NOT NULL could fail on rows null-ed by the SET NULL cascade, and
    # the nullable state is harmless (the app writes a real inviter id).
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sessions' AND column_name = 'user_agent'
                ) THEN
                    ALTER TABLE sessions DROP COLUMN user_agent;
                END IF;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sessions' AND column_name = 'id'
                ) THEN
                    ALTER TABLE sessions DROP COLUMN id;
                END IF;
            END $$;
            """
        )
    )
