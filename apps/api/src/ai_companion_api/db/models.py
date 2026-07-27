"""SQLAlchemy models — the persisted shape behind the contracts API surface.

These are richer than the contracts ``Event`` (which is the API wire format):
the DB row also carries ``embedding`` (pgvector ``Vector(384)``), ``convo_id``,
and ``created_at``. The 384 dimensions match ``memory/embeddings.py``'s
deterministic feature-hashing embedder — chosen so recall works zero-config
(no OpenAI embedding call) at the cost of semantic depth (see README).

Provider/Persona tables exist for Phase 3+ persistence; the running app still
uses the in-memory provider store from Phase 2 until the DB is wired in
``PostgresStore``. ``Usage`` feeds the Phase 4 budget dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

EMBED_DIM = 384


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_uuid() -> str:
    import uuid

    return uuid.uuid4().hex


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The server stores ONLY this opaque handle — never the key itself.
    key_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # User-selected model id (null = use the server default for this kind).
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # User-selected embedding model for semantic memory recall (null = semantic
    # memory off for this provider). Reuses the per-request BYOK key at recall.
    embeddings_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Zero-knowledge at-rest backup of the API key: base64 of
    # ``salt[16] || nonce[24] || XChaCha20-Poly1305 ciphertext``, keyed by
    # Argon2id(passphrase, salt). The server stores this column but CANNOT
    # decrypt it — the passphrase never leaves the browser. Used only to restore
    # the vault after a browser cache wipe; the per-request ECDH key flow is
    # unchanged. Null when sync isn't opted in (older rows / mock provider).
    # NOTE (2026-07-23): superseded by ``api_key_ciphertext`` below. Kept as a
    # dead column for back-comat — no longer populated by new clients.
    enc_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Server-side envelope-encrypted BYOK API key (migration 0023). Base64 of
    # ``nonce[24] || XSalsa20-Poly1305 ciphertext`` produced by
    # ``crypto/envelope.py::EnvelopeCipher`` under ``MESSENGER_TOKEN_DEK``. The
    # plaintext is the full key JSON payload (``{provider_kind, api_key,
    # base_url, extra}``) so provider extras (e.g. Bedrock's AWS triplet)
    # survive the round-trip. The server CAN decrypt this (it holds the DEK) —
    # this is envelope encryption against DB-dump exposure, NOT zero-knowledge
    # (honest disclosure, see CLAUDE.md "Security invariants"). The decrypted
    # key lives only in request scope and is zeroized after the turn. Null for
    # legacy/mock providers or when the envelope DEK is not configured.
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64))
    system_prompt: Mapped[str] = mapped_column(Text)
    tone: Mapped[dict] = mapped_column(JSON)
    opening_line: Mapped[str] = mapped_column(Text)
    custom: Mapped[bool] = mapped_column(default=False)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # NOTE: no FK on persona_id — builtin personas (sam/aria/fam/lou) are not
    # rows in ``personas`` (only custom personas are), so an FK would break the
    # majority of event rows. The user-delete cascade still reaches events via
    # the user_id FK above.
    persona_id: Mapped[str] = mapped_column(String(64), index=True)
    convo_id: Mapped[str] = mapped_column(String(64), index=True)
    prev_event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    # Multi-dimensional salience: long-term recall weight (salience), boost for
    # the immediate next turns (short_term_salience), and acute emotional charge
    # (emotional_intensity). All in [0, 1].
    salience: Mapped[float] = mapped_column(Float, default=0.0)
    short_term_salience: Mapped[float] = mapped_column(Float, default=0.0)
    emotional_intensity: Mapped[float] = mapped_column(Float, default=0.0)
    emotion_tags: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    # Which embedder produced ``embedding``: NULL = legacy feature-hashing
    # vector; a model id = semantic vector from that litellm model. The ANN
    # recall path filters on this so vectors from different spaces never mix.
    embedding_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Family scope (NULL for non-family rows). See PLAN §Family for the recall
    # contract: solo-M reads shared + (private AND participant==M); joint reads
    # shared only. The composite index serves both predicates.
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    participant_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Usage(Base):
    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # When the turn is a family session, the spend rolls up against the family
    # budget (per ``family_id``). The rollup query prefers family_id and falls
    # back to user_id for personal sessions.
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    provider_kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class Memory(Base):
    """An atomic, LLM-derived fact extracted from the event chain. The display
    unit of the /memory page. Mutated over time: extraction may update an
    existing row (refined content/tags, bumped updated_at) or supersede it
    (set ``status='superseded'`` + ``superseded_by`` to a newer row). Only
    ``status='active'`` rows are returned to the UI."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # No FK on persona_id — see Event (builtin personas are not rows here).
    persona_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    salience: Mapped[float] = mapped_column(Float, default=0.0)
    source_event_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    superseded_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Family scope — see Event. The extraction pass scopes list_memories by
    # (family_id, visibility, participant_user_id) so it only mutates memories
    # in the same scope as the current turn.
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    participant_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class MemoryShare(Base):
    """A live link letting one persona's memories be recalled by another persona
    — a *reference*, not a copy. The donor's rows stay owned by the donor (mutated
    only by the donor's own turns); the receiver's read paths union the donor's
    active memories + event chains while this row exists. Removing it revokes the
    link; nothing is duplicated or deleted.

    Donor-initiated: ``donor_persona_id`` shares INTO ``receiver_persona_id``.
    Both are opaque partition keys (builtins or ``custom-...``). The unique
    triple prevents duplicate links; ``donor == receiver`` is rejected by the
    store, not the DB (a CHECK would need the same expression on both sides)."""

    __tablename__ = "memory_shares"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "receiver_persona_id", "donor_persona_id", name="uq_memory_shares_triple"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # No FK on persona_id columns — builtins aren't rows in ``personas`` (see
    # Event). User-delete cascade still reaches shares via the user_id FK.
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    donor_persona_id: Mapped[str] = mapped_column(String(64), index=True)
    receiver_persona_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class JournalEntry(Base):
    """A user-authored diary entry — the display unit of the /journal page.

    Separate from the chat event chain (``events``): entries are written
    directly by the user, or seeded from a chat message via "Save to journal"
    (which copies the message text into a new row and links the source
    convo/event). Mood + tags are authored by the user, not generated — the
    journal surfaces them as-is ("disclose, don't perform"). ``salience`` is
    the user's "matters to me" choice, not an LLM-judged score. ``persona_id``
    is the companion this entry relates to (Lou by default).
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        # Timeline scan: ``WHERE user_id = ? ORDER BY created_at DESC LIMIT ?``
        # — a composite on (user_id, created_at) serves both the per-user feed
        # and the per-persona filter (which adds persona_id as a leading
        # equality, still served by the persona_id single-column index).
        Index("ix_journal_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # No FK on persona_id — see Event (builtin personas are not rows here).
    persona_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    mood: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # JSONB (not JSON) so the tag facet filter can use the ``@>`` containment
    # operator server-side — needed for correct pagination (a client-side tag
    # filter would miss matches on later pages). The wire/contract shape is
    # still ``list[str]``; only the Postgres column type differs.
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    salience: Mapped[float] = mapped_column(Float, default=0.0)
    source_convo_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Family scope — see Event for the recall contract.
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    participant_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class User(Base):
    """An authenticated account. Identity is linked by ``(issuer, subject)`` —
    the IdP ``sub`` for OIDC, the email for local/magic-link, or the trusted-
    header value. The ``password_hash`` (Argon2id) is set only for the local
    backend; OIDC / trusted-header / magic-link rows have it null. ``plan`` +
    ``credits_usd`` carry hosted entitlements (0/unused in self-hosted). The
    vault passphrase is NOT stored here — it never leaves the browser."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
        # Partial unique index on email so NULLs don't collide (OIDC may omit it).
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    plan: Mapped[str] = mapped_column(String(40), default="self_hosted_free")
    credits_usd: Mapped[float] = mapped_column(Float, default=0.0)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    # Family scope. NULL when the user is not in a family; each user is in at
    # most one family (enforced by application logic in routers/family.py). ON
    # DELETE SET NULL so disbanding the family (deleting the ``families`` row)
    # clears the pointer on remaining members — the user row survives.
    family_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="SET NULL"), nullable=True, index=True
    )
    family_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Session(Base):
    """An opaque session-token row backing the session cookie. Revocable
    (``revoked_at``) for logout / "sign out everywhere" / breach response, and
    expiring via ``expires_at``. The token itself is the cookie value; the cookie
    is HttpOnly + Secure + SameSite=Lax and carries no key material."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"),)

    # Surrogate id (M2): the cookie ``token`` is a secret and must never be
    # surfaced to the client — the session-list / revoke endpoints key off this
    # opaque id instead. App-generated uuid4 hex, same width as other ids.
    id: Mapped[str] = mapped_column(String(64), unique=True, default=_new_uuid)
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Captured at signup/login (M2) for the "active devices" list. Nullable for
    # rows created before the column existed and for backends that don't pass it.
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- Family tables (multi-member, real per-user accounts) ---


class Family(Base):
    """A family of up to 4 real user accounts that share a family therapist
    persona + family-scoped memory + a family BYOK key (owner's key, served to
    all members via the family vault). The owner is a regular user account; the
    family vault is a SEPARATE vault from the owner's personal vault, unlocked
    by a SEPARATE family passphrase that the owner shares with members
    out-of-band. The server cannot decrypt the family key — same zero-knowledge
    contract as the personal vault.

    The ``family_salt`` + ``family_enc_blob_seed`` columns are the only family-
    vault state the server holds; both are opaque to the server.
    """

    __tablename__ = "families"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # CASCADE (Sprint 6 user decision): deleting the owner disbands the family —
    # the families row + all family_members/family_providers/family_invites go
    # with it, and remaining members' ``users.family_id`` is SET NULL by that
    # FK. The owner is expected to disband explicitly via the family router in
    # normal operation; this is the safety net for direct user deletion.
    owner_user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Family-vault metadata. The server cannot decrypt either — both are passed
    # to members' browsers which derive the family master key with
    # Argon2id(family_passphrase, family_salt).
    family_salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    family_enc_blob_seed: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Owner-customisable system prompt for the ``fam`` persona. Read by every
    # member (so they can see what their therapist is being told); written by
    # the owner only. ``None`` means "fall back to the static ``fam`` builtin"
    # — the client keeps a copy of that builtin in its own registry, so the
    # server never has to re-ship it. Audit fields track the last setter; this
    # is intentionally a single-row overwrite (no history table for v1).
    therapist_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    therapist_prompt_set_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    therapist_prompt_set_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Owner-only flag: when true, family chat turns resolve the BYOK key from
    # the owner's personal ``providers`` row (by ``key_handle``) instead of
    # ``family_providers``. Mutually exclusive with family keys in the UI; on
    # the server, when set, the personal-provider lookup wins. The owner is
    # resolved from this row's ``owner_user_id`` — never a client-supplied
    # value — so a member cannot retarget the key lookup. No key material here.
    use_owner_personal_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )


class FamilyMember(Base):
    """A user's membership card. The labels in here (``family_display_name``,
    ``relation``, ``color``) are what the family therapist sees in the prompt
    (e.g. "Alex (parent)") — they default from ``users.display_name`` and the
    owner can edit them on the family settings page.

    Composite PK on ``(family_id, user_id)`` enforces the one-membership-per-
    user invariant at the DB level; the application also enforces at most one
    family per user via ``users.family_id``.
    """

    __tablename__ = "family_members"
    __table_args__ = (
        Index("ix_family_members_family", "family_id"),
        Index("ix_family_members_user", "user_id"),
    )

    family_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    family_role: Mapped[str] = mapped_column(String(16), nullable=False)
    family_display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FamilyInvite(Base):
    """A pending invitation to join a family. ``token_hash`` is the SHA-256 (or
    similar) of the sealed token from the email link; the server stores ONLY
    the hash so a leaked DB row doesn't hand the attacker valid invite tokens.
    The plaintext token never enters the DB. ``accepted_at`` records the
    successful accept; unaccepted past-``expires_at`` rows are rejected by the
    accept endpoint."""

    __tablename__ = "family_invites"
    __table_args__ = (Index("ix_family_invites_family_email", "family_id", "email"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SET NULL: if the inviter is deleted, pending invites they issued can still
    # be accepted (the family lives on unless the owner is the one deleted). The
    # column is nullable to allow the SET NULL cascade (was NOT NULL pre-FK).
    invited_by: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FamilyProvider(Base):
    """The family's shared BYOK row — the family owner's API key. The server
    stores ``key_handle`` plus the server-side envelope-encrypted
    ``api_key_ciphertext`` (migration 0023); the family owner's plaintext key
    is ECDH-sealed once at onboarding, decrypted in memory, re-wrapped under
    ``MESSENGER_TOKEN_DEK``, and stored here. The server CAN decrypt the family
    key (it holds the DEK) — envelope encryption against DB-dump exposure, NOT
    zero-knowledge (same honest disclosure as ``providers.api_key_ciphertext``).
    The legacy ``enc_blob`` column is kept as a dead column for back-comat."""

    __tablename__ = "family_providers"
    __table_args__ = (Index("ix_family_providers_family_created", "family_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("families.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_handle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Embedding model for family semantic memory (null = off). Metadata only.
    embeddings_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Legacy zero-knowledge at-rest backup (migration 0012). Kept as a dead
    # column — no longer populated by new clients.
    enc_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Server-side envelope-encrypted family BYOK key (migration 0023). Same
    # scheme as ``providers.api_key_ciphertext``; the same DEK protects both.
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- Billing tables (subscription purchase; hosted-only) ---
#
# The purchase flow: ``POST /v1/billing/checkout`` creates a hosted-checkout
# session at the provider (Paddle for WW, ЮKassa for RU) and records a
# ``subscriptions`` row in ``trialing``/``active`` once the first webhook
# lands. Webhooks are the SINGLE source of truth — the browser redirect does
# not mutate state. A successful payment calls ``set_user_plan`` (atomic
# UPDATE of ``users.plan`` + ``credits_usd += grant``); the existing
# ``out_of_credits`` gate and per-turn ``decrement_credits`` are unchanged.
#
# Prices are minor units (cents/kopecks) — never float. ``credits_grant_usd``
# is USD-denominated regardless of payment currency (RUB for RU plans).


class BillingPlan(Base):
    """A sellable subscription tier. Seeded by migration 0017; the web renders
    the plan grid from these rows (plus the static ``free`` tier, which is not
    a row — it's the absence of a subscription). ``geo`` selects the provider:
    ``RU`` → ЮKassa (RUB), ``WW`` → Paddle (USD/EUR). ``trial_days`` is nonzero
    only for WW (Paddle) — РФ has no trial due to 54-ФЗ."""

    __tablename__ = "billing_plans"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    geo: Mapped[str] = mapped_column(String(4), nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_grant_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The provider price id (Paddle price_id / ЮKassa template id) used to create
    # a checkout session. NULL until the plan is linked to a provider product.
    provider_price_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)


class Subscription(Base):
    """A user's subscription lifecycle. ``provider_sub_id`` is the provider's
    subscription/recurring-payment id. ``status`` is updated ONLY by webhooks.
    ``cancel_at_period_end`` is set when the user cancels via the provider
    portal — the subscription stays ``active`` until ``current_period_end``,
    then a webhook flips it to ``canceled`` and a separate expiry sweep resets
    ``users.plan`` to ``self_hosted_free``."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "provider_sub_id", name="uq_subscriptions_provider_sub"
        ),
        Index("ix_subscriptions_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_slug: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_sub_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="trialing")
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Invoice(Base):
    """A provider invoice / payment record. For РФ (ЮKassa), ``fiscal_receipt_id``
    carries the 54-ФЗ online-kassa receipt id so support can locate the cheque.
    ``receipt_url`` is the provider's hosted invoice/receipt link (shown in the
    billing tab)."""

    __tablename__ = "billing_invoices"
    __table_args__ = (Index("ix_billing_invoices_sub", "subscription_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subscription_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_invoice_id: Mapped[str] = mapped_column(String(120), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fiscal_receipt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProcessedWebhook(Base):
    """Idempotency guard for provider webhooks. INSERTed before processing
    (``ON CONFLICT DO NOTHING``); if the row already exists, the handler
    returns 200 without re-processing. The composite PK (provider, event_id)
    dedups across redeliveries — providers retry on any non-2xx, and a
    transient handler error must not double-grant credits on retry."""

    __tablename__ = "billing_webhook_events"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# --- External messengers (Telegram first; the shape generalizes) ---


class Messenger(Base):
    """A per-user external-messenger bot link (Telegram first).

    ``bot_token_ciphertext`` is the XSalsa20-Poly1305 envelope (NaCl
    ``SecretBox``, keyed by ``MESSENGER_TOKEN_DEK``) of the raw bot token —
    never plaintext at rest. ``byok_enc_blob`` is the same envelope of the
    ECDH-decrypted BYOK key material, written at handshake time (the client
    ECDH-seals to the server session pubkey; the server decrypts ONCE and
    immediately re-wraps with the envelope key). ``next_offset`` persists the
    long-polling cursor so a restart doesn't replay updates.

    Deleting the user cascades this row (migration 0022, same contract as
    providers/personas in migration 0016)."""

    __tablename__ = "messengers"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_messengers_user_kind"),
        Index(
            "ix_messengers_active",
            "status",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="pending_handshake")
    bot_token_ciphertext: Mapped[str] = mapped_column(Text)
    byok_enc_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bot_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bot_token_masked: Mapped[str] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BillingProfile(Base):
    """A user's billing profile — country + provider customer id. One row per
    user (unique). ``billing_country`` is the manual country choice that
    routes RU → ЮKassa, anything else → Paddle (NOT IP-derived). Changing
    country does not migrate an active subscription: the current one lives out
    its cycle, the new one opens at the other provider."""

    __tablename__ = "billing_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_billing_profiles_user"),)

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    default_payment_method_token: Mapped[str | None] = mapped_column(String(160), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = [
    "EMBED_DIM",
    "BillingPlan",
    "BillingProfile",
    "Event",
    "Family",
    "FamilyInvite",
    "FamilyMember",
    "FamilyProvider",
    "Invoice",
    "JournalEntry",
    "Memory",
    "MemoryShare",
    "Messenger",
    "Persona",
    "ProcessedWebhook",
    "Provider",
    "Session",
    "Subscription",
    "Usage",
    "User",
]
