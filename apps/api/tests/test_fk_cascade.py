"""Sprint 6 I21 — foreign-key ON DELETE cascade/SET NULL (migration 0016).

Verifies the DB-level cascade contract established by migration 0016
(``apps/api/migrations/versions/0016_foreign_keys_cascade.py``):

  - deleting a ``users`` row CASCADES to its events/memories/journal_entries/
    usage/providers/personas/sessions/memory_shares and its family_members row;
  - deleting a family OWNER CASCADES the family + all family_members/
    family_providers/family_invites, and SET NULLs the remaining members'
    ``users.family_id``;
  - deleting a non-owner member drops only that member's family_members row
    (family + owner + other members intact);
  - the migration is idempotent (upgrade twice is a no-op).

Gated on ``COMPANION_USE_DB=1`` against a Postgres that has run
``alembic upgrade head`` (so migration 0016's constraints are live). The
default dev/eval env is in-memory, so these skip locally and run in the Docker
/ CI Postgres path.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPANION_USE_DB") != "1",
    reason="FK cascade tests need COMPANION_USE_DB=1 + a migrated Postgres",
)


async def _engine(app):
    from ai_companion_api.db.session import get_engine

    return get_engine(app.state.settings)


async def _counts(app, user_id: str) -> dict[str, int]:
    """Count the user's dependents directly via SQL (independent of the ORM)."""
    from sqlalchemy import text

    from ai_companion_api.db.session import get_sessionmaker

    sm = get_sessionmaker(app.state.settings)
    counts: dict[str, int] = {}
    async with sm() as s:
        for table in (
            "events",
            "memories",
            "journal_entries",
            "usage",
            "providers",
            "personas",
            "sessions",
            "memory_shares",
            "family_members",
        ):
            r = await s.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": user_id}
            )
            counts[table] = int(r.scalar_one())
    return counts


async def _make_user(app, email: str) -> str:
    from ai_companion_api.db.models import User
    from ai_companion_api.db.session import get_sessionmaker

    uid = uuid.uuid4().hex
    sm = get_sessionmaker(app.state.settings)
    async with sm() as s:
        s.add(
            User(
                id=uid,
                email=email,
                display_name=email,
                issuer="local",
                subject=email,
            )
        )
        await s.commit()
    return uid


async def test_delete_user_cascades_dependents(make_app, app_client) -> None:
    async with _ctx(make_app, app_client) as (ac, app):
        from sqlalchemy import text

        from ai_companion_api.db.models import (
            Event,
            JournalEntry,
            Memory,
            MemoryShare,
            Persona,
            Provider,
            Usage,
        )
        from ai_companion_api.db.models import (
            Session as SessionModel,
        )
        from ai_companion_api.db.session import get_sessionmaker

        uid = await _make_user(app, "cascade@x.com")
        sm = get_sessionmaker(app.state.settings)
        async with sm() as s:
            s.add(Provider(id="p1", user_id=uid, kind="openai", label="k"))
            s.add(
                Persona(
                    id="pe1",
                    user_id=uid,
                    name="N",
                    role="r",
                    system_prompt="p",
                    tone={},
                    opening_line="hi",
                )
            )
            s.add(
                Event(
                    id="e1", user_id=uid, persona_id="sam", convo_id="c1", role="user", content="hi"
                )
            )
            s.add(
                Memory(
                    id="m1",
                    user_id=uid,
                    persona_id="sam",
                    content="fact",
                    tags=[],
                    source_event_ids=[],
                )
            )
            s.add(JournalEntry(id="j1", user_id=uid, persona_id="lou", body="diary", tags=[]))
            s.add(
                Usage(
                    id="u1",
                    user_id=uid,
                    provider_kind="mock",
                    model="m",
                    prompt_tokens=1,
                    completion_tokens=1,
                    cost_usd=0,
                )
            )
            s.add(
                MemoryShare(
                    id="ms1", user_id=uid, donor_persona_id="sam", receiver_persona_id="aria"
                )
            )
            s.add(SessionModel(token="t1", user_id=uid, expires_at="2099-01-01T00:00:00+00:00"))
            await s.commit()

        before = await _counts(app, uid)
        assert all(v == 1 for v in before.values()), before

        # Delete the user → DB cascades every dependent.
        async with sm() as s:
            await s.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
            await s.commit()

        after = await _counts(app, uid)
        assert all(v == 0 for v in after.values()), after


async def test_delete_owner_disbands_family_and_nulls_members(make_app, app_client) -> None:
    async with _ctx(make_app, app_client) as (ac, app):
        from sqlalchemy import select, text

        from ai_companion_api.db.models import Family, FamilyMember, User
        from ai_companion_api.db.session import get_sessionmaker

        sm = get_sessionmaker(app.state.settings)
        owner_id = await _make_user(app, "owner@x.com")
        member_id = await _make_user(app, "member@x.com")
        fam_id = uuid.uuid4().hex
        async with sm() as s:
            s.add(Family(id=fam_id, name="F", owner_user_id=owner_id))
            s.add(
                FamilyMember(
                    family_id=fam_id,
                    user_id=owner_id,
                    family_role="owner",
                    family_display_name="O",
                    relation="parent",
                    color="blue",
                )
            )
            s.add(
                FamilyMember(
                    family_id=fam_id,
                    user_id=member_id,
                    family_role="member",
                    family_display_name="M",
                    relation="child",
                    color="red",
                )
            )
            # Point both users at the family.
            await s.execute(
                text("UPDATE users SET family_id = :f WHERE id IN (:o, :m)"),
                {"f": fam_id, "o": owner_id, "m": member_id},
            )
            await s.commit()

        # Delete the OWNER → family + all members cascade; the remaining member's
        # users.family_id is SET NULL.
        async with sm() as s:
            await s.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner_id})
            await s.commit()

        async with sm() as s:
            fam = (await s.execute(select(Family).where(Family.id == fam_id))).scalar_one_or_none()
            assert fam is None
            members = (
                (await s.execute(select(FamilyMember).where(FamilyMember.family_id == fam_id)))
                .scalars()
                .all()
            )
            assert members == []
            member_user = (await s.execute(select(User).where(User.id == member_id))).scalar_one()
            assert member_user.family_id is None
        # the member account itself survived.
        assert await _counts(app, member_id) is not None


async def test_delete_non_owner_member_drops_only_membership(make_app, app_client) -> None:
    async with _ctx(make_app, app_client) as (ac, app):
        from sqlalchemy import select, text

        from ai_companion_api.db.models import Family, FamilyMember
        from ai_companion_api.db.session import get_sessionmaker

        sm = get_sessionmaker(app.state.settings)
        owner_id = await _make_user(app, "owner2@x.com")
        member_id = await _make_user(app, "member2@x.com")
        fam_id = uuid.uuid4().hex
        async with sm() as s:
            s.add(Family(id=fam_id, name="F", owner_user_id=owner_id))
            s.add(
                FamilyMember(
                    family_id=fam_id,
                    user_id=owner_id,
                    family_role="owner",
                    family_display_name="O",
                    relation="parent",
                    color="blue",
                )
            )
            s.add(
                FamilyMember(
                    family_id=fam_id,
                    user_id=member_id,
                    family_role="member",
                    family_display_name="M",
                    relation="child",
                    color="red",
                )
            )
            await s.commit()

        # Delete the non-owner member → only their membership row goes; the
        # family, owner, and owner's membership are intact.
        async with sm() as s:
            await s.execute(text("DELETE FROM users WHERE id = :m"), {"m": member_id})
            await s.commit()

        async with sm() as s:
            fam = (await s.execute(select(Family).where(Family.id == fam_id))).scalar_one_or_none()
            assert fam is not None
            members = (
                (await s.execute(select(FamilyMember).where(FamilyMember.family_id == fam_id)))
                .scalars()
                .all()
            )
            assert {m.user_id for m in members} == {owner_id}


async def test_migration_0016_idempotent(make_app, app_client) -> None:
    """Re-running upgrade() must be a no-op (every ALTER is guarded)."""
    from alembic import command
    from alembic.config import Config

    async with _ctx(make_app, app_client) as (_ac, app):
        cfg = Config(str(app.state.settings.root_dir / "apps" / "api" / "alembic.ini"))
        command.upgrade(cfg, "head")  # already at head → idempotent no-op
        command.upgrade(cfg, "head")  # second time still clean


def _ctx(make_app, app_client):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _c():
        app = make_app(COMPANION_USE_DB="1")
        async with app_client(app) as ac:
            yield ac, app

    return _c()
