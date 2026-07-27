"""Family-scope recall invariants (the security-critical core).

The family feature introduces three orthogonal scope columns on events /
memories / journal: ``family_id``, ``visibility`` ("private"/"shared"), and
``participant_user_id``. The recall predicates (in ``memory/store.py``) are the
ONLY place privacy is enforced; this test exercises the matrix of cases that
must hold for the system to honor the documented honest limits:

  - Solo-M recall:    shared ∪ (private AND participant == M)
  - Joint recall:     shared only (private never leaks)
  - Cross-family:     F1 cannot see F2's rows
  - Cross-user:       M1's private cannot be seen by M2

These tests are unit-level against ``InMemoryStore`` — the same store the app
uses by default. The Postgres impl reuses the same ``_apply_family_scope``
helper, so passing the predicate here is necessary (and the contracts check
ensures the wire shape is right).
"""

from __future__ import annotations

import pytest
from ai_companion_contracts import EventRole, MemoryStatus

from ai_companion_api.memory import InMemoryStore, append_event

USER_A = "user-a"
USER_B = "user-b"
FAM = "fam-1"
OTHER_FAM = "fam-2"
PERSONA = "fam"


async def _seed_solo(store: InMemoryStore, *, user_id: str, family_id: str, text: str) -> str:
    """Append a single private event in family ``family_id`` for ``user_id``."""
    ev = await append_event(
        store,
        user_id=user_id,
        persona_id=PERSONA,
        convo_id=f"convo-{user_id}",
        role=EventRole.user,
        content=text,
        family_id=family_id,
        visibility="private",
        participant_user_id=user_id,
    )
    return ev.id


async def _seed_shared(store: InMemoryStore, *, user_id: str, family_id: str, text: str) -> str:
    ev = await append_event(
        store,
        user_id=user_id,
        persona_id=PERSONA,
        convo_id=f"convo-joint-{user_id}",
        role=EventRole.user,
        content=text,
        family_id=family_id,
        visibility="shared",
        participant_user_id=user_id,
    )
    return ev.id


# --- solo-M recall ---------------------------------------------------------


@pytest.mark.asyncio
async def test_solo_recall_includes_own_private_and_shared() -> None:
    store = InMemoryStore()
    own_private = await _seed_solo(
        store, user_id=USER_A, family_id=FAM, text="I have a private secret."
    )
    # Note: the family-shared event in this family can be written by any
    # member, but list_events is scoped to the principal's user_id — so we
    # use USER_A (the perspective the recall is being computed for). For the
    # cross-member private exclusion, we use _seed_solo for USER_B below.
    shared = await _seed_shared(
        store, user_id=USER_A, family_id=FAM, text="We agreed to talk about X."
    )
    rows = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    ids = {e.id for e in rows}
    assert own_private in ids  # own private
    assert shared in ids  # shared always visible in solo


@pytest.mark.asyncio
async def test_solo_recall_excludes_other_member_private() -> None:
    store = InMemoryStore()
    other_private = await _seed_solo(
        store, user_id=USER_B, family_id=FAM, text="B's private disclosure."
    )
    rows = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    assert all(e.id != other_private for e in rows), (
        "A's solo recall must NOT include B's private disclosures"
    )


# --- joint recall (visibility == shared) ------------------------------------


@pytest.mark.asyncio
async def test_joint_recall_includes_only_shared() -> None:
    store = InMemoryStore()
    a_private = await _seed_solo(store, user_id=USER_A, family_id=FAM, text="A's secret.")
    b_private = await _seed_solo(store, user_id=USER_B, family_id=FAM, text="B's secret.")
    shared = await _seed_shared(
        store, user_id=USER_A, family_id=FAM, text="We agreed to talk about X."
    )
    rows = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    ids = {e.id for e in rows}
    assert shared in ids
    assert a_private not in ids, "A's private MUST NOT leak into joint session"
    assert b_private not in ids, "B's private MUST NOT leak into joint session"


@pytest.mark.asyncio
async def test_joint_recall_for_b_user_excludes_a_private() -> None:
    store = InMemoryStore()
    a_private = await _seed_solo(store, user_id=USER_A, family_id=FAM, text="A's private.")
    shared = await _seed_shared(store, user_id=USER_B, family_id=FAM, text="A shared message.")
    rows = await store.list_events(
        user_id=USER_B,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_B,
    )
    ids = {e.id for e in rows}
    assert shared in ids
    assert a_private not in ids


@pytest.mark.asyncio
async def test_joint_shared_visible_to_other_member() -> None:
    """The joint-session fix: a shared event authored by USER_B in the
    family's one shared joint convo must be visible to USER_A's joint read
    (list_events AND recent_window). Before the fix, the ``user_id == requester``
    ownership pre-filter dropped B's rows before ``_apply_family_scope`` ran,
    so each member only saw their own messages in the shared thread — the
    reported bug. Also pins that the relaxation is gated on ``visibility ==
    "shared"``: A's solo (private) read still excludes B's private rows."""
    store = InMemoryStore()
    joint_convo = f"fam-joint-{FAM}"
    # B authors a shared message in the joint convo.
    b_shared = await append_event(
        store,
        user_id=USER_B,
        persona_id=PERSONA,
        convo_id=joint_convo,
        role=EventRole.user,
        content="B speaking in the joint thread.",
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_B,
    )
    b_shared_id = b_shared.id
    # B also has a private solo disclosure A must never see.
    b_private = await _seed_solo(store, user_id=USER_B, family_id=FAM, text="B's secret.")

    # A's joint list_events sees B's shared message.
    rows = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        convo_id=joint_convo,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    ids = {e.id for e in rows}
    assert b_shared_id in ids, "A's joint read MUST see B's shared message"
    assert b_private not in ids, "B's private MUST NOT leak into the joint session"

    # A's joint recent_window (the therapist's LLM context path) sees it too.
    window = await store.recent_window(
        user_id=USER_A,
        persona_id=PERSONA,
        convo_id=joint_convo,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    assert b_shared_id in {e.id for e in window}, (
        "A's joint recent_window MUST include B's shared message (therapist context)"
    )

    # Relaxation is gated on shared: A's SOLO read excludes B's private rows.
    solo = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    assert b_private not in {e.id for e in solo}, (
        "A's solo read MUST NOT include B's private 1:1 disclosures"
    )


# --- cross-family isolation -------------------------------------------------


@pytest.mark.asyncio
async def test_cross_family_isolation() -> None:
    store = InMemoryStore()
    f1_shared = await _seed_shared(store, user_id=USER_A, family_id=FAM, text="F1 shared.")
    f2_shared = await _seed_shared(store, user_id=USER_A, family_id=OTHER_FAM, text="F2 shared.")
    # User A viewing F1 must not see F2's rows.
    rows = await store.list_events(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    ids = {e.id for e in rows}
    assert f1_shared in ids
    assert f2_shared not in ids


# --- donor MemoryShare is bounded by the family scope (PLAN §16 #1, #6) ---


@pytest.mark.asyncio
async def test_family_solo_recall_excludes_donor_events() -> None:
    """A solo family recall (``family_id + visibility=private``) MUST NOT
    union donor-share rows. Donors are cross-persona (same user) and have no
    family-scope semantics — a donor's events are personal, surfacing them
    in a family solo session would leak across the family boundary.

    This is the post-MVP invariant: donor MemoryShare stays personal-scope
    only. A family session sees the family's own events + memories.
    """
    from datetime import UTC, datetime

    from ai_companion_contracts import Event, EventRole

    store = InMemoryStore()
    # A donor event under user A's own "aria" persona (personal scope).
    donor_event = Event(
        id="donor-e1",
        user_id=USER_A,
        persona_id="aria",
        role=EventRole.user,
        content="A's donor memory: my dog Maple died.",
        salience=0.7,
        created_at=datetime.now(UTC),
    )
    await store.add_event(donor_event)
    # Wire the share: aria → fam.
    await store.add_share(user_id=USER_A, donor_persona_id="aria", receiver_persona_id=PERSONA)
    # In personal scope (no family_id), the donor event is unioned as before.
    own_personal = await store.recall_candidates(user_id=USER_A, persona_id=PERSONA)
    assert any(e.id == "donor-e1" for e in own_personal), (
        "personal recall should still union donor rows"
    )
    # In family-scope recall (family_id + visibility), donor rows are
    # excluded — the family sees only its own events.
    fam = await store.recall_candidates(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    assert not any(e.id == "donor-e1" for e in fam), (
        "family-scope recall MUST exclude donor events (PLAN §16 #1, #6)"
    )
    # And the joint family recall likewise excludes the donor.
    joint = await store.recall_candidates(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    assert not any(e.id == "donor-e1" for e in joint)


@pytest.mark.asyncio
async def test_family_list_memories_excludes_donor() -> None:
    """Mirror of the event invariant for ``list_memories`` — donor memories
    never surface in a family-scope list, even when the share is active."""
    from datetime import UTC, datetime

    from ai_companion_contracts import Memory, MemoryStatus

    store = InMemoryStore()
    donor_mem = Memory(
        id="donor-m1",
        user_id=USER_A,
        persona_id="aria",
        content="Donor memory: project deadline Friday.",
        tags=["work"],
        salience=0.9,
        source_event_ids=[],
        status=MemoryStatus.active,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await store.add_memory(donor_mem)
    await store.add_share(user_id=USER_A, donor_persona_id="aria", receiver_persona_id=PERSONA)
    # Personal: donor is unioned.
    personal = await store.list_memories(user_id=USER_A, persona_id=PERSONA)
    assert any(m.id == "donor-m1" for m in personal)
    # Family-scope: donor excluded.
    fam_private = await store.list_memories(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    assert not any(m.id == "donor-m1" for m in fam_private)
    fam_shared = await store.list_memories(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    assert not any(m.id == "donor-m1" for m in fam_shared)


# --- personal (no family) is unaffected -------------------------------------


@pytest.mark.asyncio
async def test_no_family_scope_returns_all_own_rows() -> None:
    store = InMemoryStore()
    a_personal = await append_event(
        store,
        user_id=USER_A,
        persona_id="aria",
        convo_id="personal",
        role=EventRole.user,
        content="personal chat",
    )
    a_fam = await _seed_solo(store, user_id=USER_A, family_id=FAM, text="family chat")
    # No family_id → no scope filter, all of A's own aria rows are visible.
    rows = await store.list_events(user_id=USER_A, persona_id="aria")
    ids = {e.id for e in rows}
    assert a_personal.id in ids
    # Different persona — the family event is on persona "fam" so it isn't
    # surfaced here at all (list_events is per-persona, not family).
    assert a_fam not in ids


# --- memory-store parity: list_memories honors the same predicate ----------


@pytest.mark.asyncio
async def test_list_memories_joint_excludes_private() -> None:
    store = InMemoryStore()
    # Manually construct memories because we don't run extraction here.
    from ai_companion_contracts import Memory

    priv = Memory(
        id="m-priv",
        user_id=USER_A,
        persona_id=PERSONA,
        content="A's private memory.",
        tags=[],
        salience=0.8,
        status=MemoryStatus.active,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        family_id=FAM,
        visibility="private",
        participant_user_id=USER_A,
    )
    shared = Memory(
        id="m-shared",
        user_id=USER_A,
        persona_id=PERSONA,
        content="Family shared memory.",
        tags=[],
        salience=0.8,
        status=MemoryStatus.active,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    await store.add_memory(priv)
    await store.add_memory(shared)
    rows = await store.list_memories(
        user_id=USER_A,
        persona_id=PERSONA,
        family_id=FAM,
        visibility="shared",
        participant_user_id=USER_A,
    )
    ids = {m.id for m in rows}
    assert "m-shared" in ids
    assert "m-priv" not in ids


# --- wipe paths --------------------------------------------------------------


@pytest.mark.asyncio
async def test_wipe_member_in_family_keeps_shared() -> None:
    store = InMemoryStore()
    a_private = await _seed_solo(store, user_id=USER_A, family_id=FAM, text="A's private.")
    # Shared is recorded on USER_A's account too (each member's event chain
    # carries the shared message they wrote). wipe_member_in_family deletes
    # only A's PRIVATE rows; A's own shared rows persist.
    shared = await _seed_shared(store, user_id=USER_A, family_id=FAM, text="shared.")
    await store.wipe_member_in_family(family_id=FAM, user_id=USER_A)
    rows = await store.list_events(user_id=USER_A, persona_id=PERSONA, family_id=FAM)
    ids = {e.id for e in rows}
    assert a_private not in ids
    assert shared in ids, "shared layer must survive member's leave"


@pytest.mark.asyncio
async def test_wipe_family_scope_removes_everything_in_family() -> None:
    store = InMemoryStore()
    a_private = await _seed_solo(store, user_id=USER_A, family_id=FAM, text="A's private.")
    b_shared = await _seed_shared(store, user_id=USER_B, family_id=FAM, text="shared.")
    await store.wipe_family_scope(family_id=FAM)
    # Disband: NOTHING in family FAM remains. Other-family rows are untouched.
    rows_a = await store.list_events(user_id=USER_A, persona_id=PERSONA, family_id=FAM)
    assert all(e.id not in {a_private, b_shared} for e in rows_a)
