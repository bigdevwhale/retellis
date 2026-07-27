"""Family persistence — multi-member families, invites, family-vault metadata,
family-scoped provider rows.

Mirrors ``auth/store.py`` and ``memory/store.py``: one ``FamilyStore`` Protocol
with an in-memory implementation (zero-config default, tests, graceful
fallback when the DB is unreachable) and a Postgres implementation using the
shared async session factory from ``db.session``. Picked by
``make_family_store(settings)`` on the same Postgres-vs-in-memory axis as the
auth + memory stores.

The family is a SEPARATE scope from the personal vault: the family BYOK key
(``family_providers.enc_blob``) is sealed to a family passphrase, not the
owner's personal passphrase. The server never sees the family passphrase
and cannot decrypt the family blob — same zero-knowledge contract as the
personal ``providers.enc_blob`` (migration 0007). Members' browsers derive
the family master key with ``Argon2id(family_passphrase, family_salt)``.

All cross-user family methods return 404 (not 403) for the wrong tenant —
the lookup is scoped by the principal's ``user_id`` so a member of one
family cannot enumerate another family's data (CLAUDE.md, "Endpoints that
cross users return 404").
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from ai_companion_contracts import (
    Family,
    FamilyInvite,
    FamilyMember,
    FamilyProvider,
    FamilyRole,
    ProviderKind,
)

from ..clock import utcnow as clock_utcnow
from ..config import Settings

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    # Strictly monotonic (clock.utcnow) — Windows datetime.now(UTC) has ~1ms
    # resolution, so two fast saves could tie on audit timestamps (set_at)
    # and make "fresh timestamp on every save" non-deterministic.
    return clock_utcnow()


# --- Records (auth/memory store style: dataclasses, separate from ORM/wire) ---


@dataclass
class _VaultSeed:
    """Family-vault state held by the server. Both fields are opaque — the
    server cannot decrypt ``family_enc_blob_seed``. ``family_salt`` is needed
    by each member's browser to derive the master key. The seed envelope
    itself contains no key material; it's a sentinel that lets the client
    tell whether the family vault has been initialized."""

    family_salt: str | None = None
    family_enc_blob_seed: str | None = None


@dataclass
class _TherapistPrompt:
    """Owner-customised system prompt for the ``fam`` persona, persisted on
    the family row. The body is plaintext (it's owner-authored shared
    content, not a key — same disclosure regime as the custom-persona
    prompt, NOT zero-knowledge like the family BYOK key). ``set_by_user_id``
    + ``set_at`` are the single-row audit (no history table for v1)."""

    body: str | None = None
    set_by_user_id: str | None = None
    set_at: datetime | None = None


@dataclass
class FamilyInviteRecord:
    id: str
    family_id: str
    email: str
    role: str
    token_hash: str
    expires_at: datetime
    accepted_at: datetime | None
    invited_by: str
    created_at: datetime


@runtime_checkable
class FamilyStore(Protocol):
    """Async family store. All methods are awaitable.

    Conventions:
    - All reads scoped by ``user_id`` return None (not raise) for cross-family
      access so the caller can serve 404.
    - All writes check family membership + role and raise ``FamilyStoreError``
      with a status code; the router maps that to HTTP."""

    # --- family CRUD ---
    async def create_family(self, *, name: str, owner_user_id: str) -> Family: ...
    async def get_family_for_user(
        self, *, user_id: str, preferred_family_id: str | None = None
    ) -> Family | None: ...
    async def get_family(self, *, family_id: str) -> Family | None: ...
    async def rename_family(self, *, family_id: str, name: str) -> Family | None: ...
    async def disband_family(self, *, family_id: str) -> bool: ...
    # Owner-only toggle: when true, family turns resolve the BYOK key from the
    # owner's personal providers row (by key_handle) instead of family_providers.
    # Returns the updated family (or None if the family does not exist).
    async def set_use_owner_personal_key(
        self, *, family_id: str, value: bool
    ) -> Family | None: ...

    # --- members ---
    async def list_members(self, *, family_id: str) -> list[FamilyMember]: ...
    async def add_member(
        self,
        *,
        family_id: str,
        user_id: str,
        family_role: FamilyRole,
        family_display_name: str,
        relation: str,
        color: str,
    ) -> FamilyMember: ...
    async def remove_member(self, *, family_id: str, user_id: str) -> bool: ...
    async def is_member(self, *, family_id: str, user_id: str) -> bool: ...

    # --- invites ---
    async def create_invite(
        self,
        *,
        family_id: str,
        email: str,
        role: FamilyRole,
        token_hash: str,
        invited_by: str,
        expires_at: datetime,
    ) -> FamilyInvite: ...
    async def list_invites(self, *, family_id: str) -> list[FamilyInvite]: ...
    async def get_invite_by_hash(self, *, token_hash: str) -> FamilyInviteRecord | None: ...
    async def get_invite(self, *, invite_id: str) -> FamilyInviteRecord | None: ...
    async def mark_invite_accepted(self, *, invite_id: str) -> bool: ...
    async def delete_invite(self, *, invite_id: str) -> bool: ...
    async def find_pending_invite_for_email(
        self, *, family_id: str, email: str
    ) -> FamilyInviteRecord | None: ...
    async def consume_pending_invite_for_email(
        self, *, email: str
    ) -> FamilyInviteRecord | None: ...
    # --- single-use consumed tokens (post-MVP hardening, PLAN §16 #2) ---
    # ``consume_invite_token`` is the canonical "mark a token as used" op. The
    # router calls it BEFORE the ``get_invite_by_hash`` lookup so a replayed
    # token 410s immediately, even after the underlying invite row has been
    # marked accepted (or deleted).
    async def consume_invite_token(self, *, token_hash: str) -> bool: ...

    # --- vault seed (server holds opaque metadata only) ---
    async def get_vault_seed(self, *, family_id: str) -> _VaultSeed: ...
    async def set_vault_seed(
        self,
        *,
        family_id: str,
        family_salt: str | None,
        family_enc_blob_seed: str | None,
    ) -> None: ...

    # --- family therapist prompt (owner-write, member-read) ---
    # Persisted on the family row. ``body is None`` means "no customisation" —
    # the client falls back to the static ``fam`` builtin (mirrored from the
    # server registry). ``set_by_user_id`` + ``set_at`` are the single-row
    # audit. The router resolves ``set_by_display_name`` from the auth store
    # at read time so the wire shape is self-contained.
    async def get_therapist_prompt(self, *, family_id: str) -> _TherapistPrompt: ...
    async def set_therapist_prompt(
        self,
        *,
        family_id: str,
        body: str | None,
        set_by_user_id: str,
    ) -> _TherapistPrompt: ...

    # --- family providers (server-side envelope-encrypted family key) ---
    # ``api_key_ciphertext`` is the envelope-encrypted family BYOK key
    # (migration 0023). Passed as a separate kwarg — never on the contract
    # ``FamilyProvider`` model. ``get_family_provider_api_key_ciphertext`` is
    # the per-turn resolution read keyed by ``(family_id, key_handle)``.
    async def add_family_provider(
        self, *, p: FamilyProvider, api_key_ciphertext: str | None = None
    ) -> FamilyProvider: ...
    async def list_family_providers(self, *, family_id: str) -> list[FamilyProvider]: ...
    async def get_family_provider(
        self, *, family_id: str, provider_id: str
    ) -> FamilyProvider | None: ...
    async def get_family_provider_api_key_ciphertext(
        self, *, family_id: str, key_handle: str
    ) -> str | None: ...
    # ``embeddings_model`` follows this store's None=keep convention; an empty
    # string clears the column (turns family semantic memory off) — the family
    # PATCH surface has no explicit-null channel like the personal one.
    async def update_family_provider(
        self,
        *,
        family_id: str,
        provider_id: str,
        label: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        embeddings_model: str | None = None,
    ) -> FamilyProvider | None: ...
    async def set_family_provider_enc_blob(
        self,
        *,
        family_id: str,
        provider_id: str,
        enc_blob: str | None = None,
        api_key_ciphertext: str | None = None,
    ) -> FamilyProvider | None: ...
    async def delete_family_provider(self, *, family_id: str, provider_id: str) -> bool: ...

    # --- memory scope wipes ---
    # See PLAN §Family. ``wipe_member_in_family`` clears that member's private
    # in the family scope; shared layer untouched. ``wipe_family_scope`` clears
    # ALL family-scoped data and is used by disband.
    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None: ...
    async def wipe_family_scope(self, *, family_id: str) -> None: ...

    async def table_exists(self) -> bool: ...


class FamilyStoreError(Exception):
    """Raised by the store for cross-family access / wrong-role mutations.
    The router maps ``status_code`` to HTTP."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- in-memory implementation ---


class InMemoryFamilyStore:
    """Process-local family store — zero-config default + test fixture."""

    def __init__(self) -> None:
        self._families: dict[str, Family] = {}
        self._members: dict[tuple[str, str], FamilyMember] = {}  # (family_id, user_id)
        self._invites: dict[str, FamilyInviteRecord] = {}
        self._vault_seeds: dict[str, _VaultSeed] = {}
        # Per-family owner-authored therapist prompt (or None for "use the
        # static ``fam`` builtin"). Single-row overwrite; no history table.
        self._therapist_prompts: dict[str, _TherapistPrompt] = {}
        self._providers: dict[str, FamilyProvider] = {}
        # Server-side envelope-encrypted family BYOK key ciphertext (migration
        # 0023), keyed by family provider id. Kept separate from the contract
        # ``FamilyProvider`` objects so the ciphertext is never returned.
        self._family_provider_api_key_ciphertext: dict[str, str] = {}
        # Single-use consumed tokens (post-MVP hardening, PLAN §16 #2). On the
        # Postgres side this maps to the ``consumed_tokens`` table (migration
        # 0013); here it's a set so the in-memory store honors the same rule.
        self._consumed: set[str] = set()

    # --- family CRUD ---
    async def create_family(self, *, name: str, owner_user_id: str) -> Family:
        fid = uuid.uuid4().hex
        fam = Family(id=fid, name=name, owner_user_id=owner_user_id, created_at=_utcnow())
        self._families[fid] = fam
        # Owner is materialized as a family_members row too so list_members
        # and the recall-attribution lookup treat them uniformly.
        self._members[(fid, owner_user_id)] = FamilyMember(
            family_id=fid,
            user_id=owner_user_id,
            family_role=FamilyRole.owner,
            family_display_name="",  # filled by the router from users.display_name
            relation="other",
            color="#7c3aed",
            joined_at=_utcnow(),
        )
        self._vault_seeds[fid] = _VaultSeed()
        return fam

    async def get_family_for_user(
        self, *, user_id: str, preferred_family_id: str | None = None
    ) -> Family | None:
        # The "one family per user" invariant at the application level is
        # ``users.family_id``: the user has exactly one *current* family, and
        # ``users.family_id`` is the authoritative pointer. The
        # ``family_members`` table can in principle hold older rows (e.g. a
        # disbanded family where the row was not cleaned up, or a previous
        # membership that was never explicitly left). When a user is a
        # member of several families, we MUST return the one that matches
        # ``users.family_id`` (the caller's preferred family) — otherwise
        # the LLM stream endpoint sees a body ``family_id`` that disagrees
        # with ``principal.family_id`` and 404s on the cross-family check.
        #
        # Order:
        # 1. If ``preferred_family_id`` is set AND the user is a member of
        #    that family, return it.
        # 2. Otherwise fall back to the most recent membership (by
        #    ``joined_at DESC``), which is at least deterministic and
        #    matches the most recent intent.
        if preferred_family_id is not None:
            m = self._members.get((preferred_family_id, user_id))
            if m is not None:
                fam = self._families.get(preferred_family_id)
                if fam is not None:
                    return fam
        candidates = [
            (self._families[fid], m.joined_at)
            for (fid, uid), m in self._members.items()
            if uid == user_id and fid in self._families
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[1], reverse=True)
        return candidates[0][0]

    async def get_family(self, *, family_id: str) -> Family | None:
        return self._families.get(family_id)

    async def rename_family(self, *, family_id: str, name: str) -> Family | None:
        fam = self._families.get(family_id)
        if fam is None:
            return None
        self._families[family_id] = fam.model_copy(update={"name": name})
        return self._families[family_id]

    async def set_use_owner_personal_key(
        self, *, family_id: str, value: bool
    ) -> Family | None:
        fam = self._families.get(family_id)
        if fam is None:
            return None
        self._families[family_id] = fam.model_copy(
            update={"use_owner_personal_key": value}
        )
        return self._families[family_id]

    async def disband_family(self, *, family_id: str) -> bool:
        if family_id not in self._families:
            return False
        await self.wipe_family_scope(family_id=family_id)
        # Wipe already removed families/members/invites/providers; just drop the
        # family row + vault seed + therapist prompt so a re-created family
        # with the same id starts blank.
        del self._families[family_id]
        self._vault_seeds.pop(family_id, None)
        self._therapist_prompts.pop(family_id, None)
        return True

    # --- members ---
    async def list_members(self, *, family_id: str) -> list[FamilyMember]:
        return [m for (fid, _), m in self._members.items() if fid == family_id]

    async def add_member(
        self,
        *,
        family_id: str,
        user_id: str,
        family_role: FamilyRole,
        family_display_name: str,
        relation: str,
        color: str,
    ) -> FamilyMember:
        member = FamilyMember(
            family_id=family_id,
            user_id=user_id,
            family_role=family_role,
            family_display_name=family_display_name,
            relation=relation,
            color=color,
            joined_at=_utcnow(),
        )
        self._members[(family_id, user_id)] = member
        return member

    async def remove_member(self, *, family_id: str, user_id: str) -> bool:
        return self._members.pop((family_id, user_id), None) is not None

    async def is_member(self, *, family_id: str, user_id: str) -> bool:
        return (family_id, user_id) in self._members

    # --- invites ---
    async def create_invite(
        self,
        *,
        family_id: str,
        email: str,
        role: FamilyRole,
        token_hash: str,
        invited_by: str,
        expires_at: datetime,
    ) -> FamilyInvite:
        iid = uuid.uuid4().hex
        rec = FamilyInviteRecord(
            id=iid,
            family_id=family_id,
            email=email.lower(),
            role=role.value,
            token_hash=token_hash,
            expires_at=expires_at,
            accepted_at=None,
            invited_by=invited_by,
            created_at=_utcnow(),
        )
        self._invites[iid] = rec
        return FamilyInvite(
            id=iid,
            family_id=family_id,
            email=rec.email,
            role=FamilyRole(rec.role),
            expires_at=expires_at,
            created_at=rec.created_at,
            accepted_at=None,
            invited_by=invited_by,
        )

    async def list_invites(self, *, family_id: str) -> list[FamilyInvite]:
        return [
            FamilyInvite(
                id=r.id,
                family_id=r.family_id,
                email=r.email,
                role=FamilyRole(r.role),
                expires_at=r.expires_at,
                created_at=r.created_at,
                accepted_at=r.accepted_at,
                invited_by=r.invited_by,
            )
            for r in self._invites.values()
            if r.family_id == family_id
        ]

    async def get_invite_by_hash(self, *, token_hash: str) -> FamilyInviteRecord | None:
        for r in self._invites.values():
            if r.token_hash == token_hash:
                return r
        return None

    async def get_invite(self, *, invite_id: str) -> FamilyInviteRecord | None:
        return self._invites.get(invite_id)

    async def mark_invite_accepted(self, *, invite_id: str) -> bool:
        r = self._invites.get(invite_id)
        if r is None:
            return False
        if r.accepted_at is not None:
            return False
        self._invites[invite_id] = FamilyInviteRecord(
            id=r.id,
            family_id=r.family_id,
            email=r.email,
            role=r.role,
            token_hash=r.token_hash,
            expires_at=r.expires_at,
            accepted_at=_utcnow(),
            invited_by=r.invited_by,
            created_at=r.created_at,
        )
        return True

    async def delete_invite(self, *, invite_id: str) -> bool:
        return self._invites.pop(invite_id, None) is not None

    async def find_pending_invite_for_email(
        self, *, family_id: str, email: str
    ) -> FamilyInviteRecord | None:
        e = email.lower()
        for r in self._invites.values():
            if (
                r.family_id == family_id
                and r.email == e
                and r.accepted_at is None
                and r.expires_at > _utcnow()
            ):
                return r
        return None

    async def consume_pending_invite_for_email(self, *, email: str) -> FamilyInviteRecord | None:
        """Return the first pending (not expired, not accepted) invite for this
        email. Used by the magiclink auto-attach path. The caller still has to
        explicitly accept (this just locates the row)."""
        e = email.lower()
        for r in self._invites.values():
            if r.email == e and r.accepted_at is None and r.expires_at > _utcnow():
                return r
        return None

    # --- single-use consumed tokens (post-MVP hardening) ---
    async def consume_invite_token(self, *, token_hash: str) -> bool:
        """Atomically record a token as used. Returns True on the first call,
        False on replay. On Postgres this is ``INSERT ... ON CONFLICT DO
        NOTHING``; here we lean on the set's atomicity. The router calls this
        BEFORE ``get_invite_by_hash`` so a replayed token 410s immediately
        even if the invite row has already been marked accepted_at."""
        if token_hash in self._consumed:
            return False
        self._consumed.add(token_hash)
        return True

    # --- vault seed ---
    async def get_vault_seed(self, *, family_id: str) -> _VaultSeed:
        return self._vault_seeds.get(family_id, _VaultSeed())

    async def set_vault_seed(
        self,
        *,
        family_id: str,
        family_salt: str | None,
        family_enc_blob_seed: str | None,
    ) -> None:
        self._vault_seeds[family_id] = _VaultSeed(
            family_salt=family_salt, family_enc_blob_seed=family_enc_blob_seed
        )

    # --- therapist prompt ---
    async def get_therapist_prompt(self, *, family_id: str) -> _TherapistPrompt:
        """Read the owner-saved family therapist prompt. Returns a default
        ``_TherapistPrompt`` (all fields None) when the family has not
        customised the prompt — the client falls back to its own copy of the
        static ``fam`` builtin, so the wire never has to re-ship the long
        builtin. The store returns the same default for unknown families;
        the router already gated on ``_require_member`` so this is safe."""
        return self._therapist_prompts.get(family_id, _TherapistPrompt())

    async def set_therapist_prompt(
        self,
        *,
        family_id: str,
        body: str | None,
        set_by_user_id: str,
    ) -> _TherapistPrompt:
        """Write (or clear, with ``body=None``) the family's therapist prompt.
        The store stamps the audit timestamp server-side so the wire is
        self-consistent across replicas / multi-instance deployments — the
        caller never supplies ``set_at``."""
        rec = _TherapistPrompt(
            body=body,
            set_by_user_id=set_by_user_id,
            set_at=_utcnow(),
        )
        self._therapist_prompts[family_id] = rec
        return rec

    # --- family providers ---
    async def add_family_provider(
        self, *, p: FamilyProvider, api_key_ciphertext: str | None = None
    ) -> FamilyProvider:
        self._providers[p.id] = p
        if api_key_ciphertext is not None:
            self._family_provider_api_key_ciphertext[p.id] = api_key_ciphertext
        return p

    async def list_family_providers(self, *, family_id: str) -> list[FamilyProvider]:
        return [p for p in self._providers.values() if p.family_id == family_id]

    async def get_family_provider(
        self, *, family_id: str, provider_id: str
    ) -> FamilyProvider | None:
        p = self._providers.get(provider_id)
        if p is None or p.family_id != family_id:
            return None
        return p

    async def get_family_provider_api_key_ciphertext(
        self, *, family_id: str, key_handle: str
    ) -> str | None:
        for p in self._providers.values():
            if p.family_id == family_id and p.key_handle == key_handle:
                return self._family_provider_api_key_ciphertext.get(p.id)
        return None

    async def update_family_provider(
        self,
        *,
        family_id: str,
        provider_id: str,
        label: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        embeddings_model: str | None = None,
    ) -> FamilyProvider | None:
        p = await self.get_family_provider(family_id=family_id, provider_id=provider_id)
        if p is None:
            return None
        updated = p.model_copy(
            update={
                "label": label if label is not None else p.label,
                "base_url": base_url if base_url is not None else p.base_url,
                "model": model if model is not None else p.model,
                # None = keep; "" = clear (family semantic memory off).
                "embeddings_model": (
                    p.embeddings_model
                    if embeddings_model is None
                    else (embeddings_model or None)
                ),
            }
        )
        self._providers[provider_id] = updated
        return updated

    async def set_family_provider_enc_blob(
        self,
        *,
        family_id: str,
        provider_id: str,
        enc_blob: str | None = None,
        api_key_ciphertext: str | None = None,
    ) -> FamilyProvider | None:
        p = await self.get_family_provider(family_id=family_id, provider_id=provider_id)
        if p is None:
            return None
        update_kwargs: dict[str, object] = {}
        if enc_blob is not None:
            update_kwargs["enc_blob"] = enc_blob
        updated = p.model_copy(update=update_kwargs) if update_kwargs else p
        self._providers[provider_id] = updated
        if api_key_ciphertext is not None:
            self._family_provider_api_key_ciphertext[provider_id] = api_key_ciphertext
        return updated

    async def delete_family_provider(self, *, family_id: str, provider_id: str) -> bool:
        p = self._providers.get(provider_id)
        if p is None or p.family_id != family_id:
            return False
        del self._providers[provider_id]
        self._family_provider_api_key_ciphertext.pop(provider_id, None)
        return True

    # --- scope wipes (no-op for in-memory: caller wipes the memory store) ---
    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None:
        # The actual data wipe happens in the memory store (events/memories/
        # journal). Here we just drop the membership card.
        self._members.pop((family_id, user_id), None)

    async def wipe_family_scope(self, *, family_id: str) -> None:
        self._members = {k: v for k, v in self._members.items() if k[0] != family_id}
        self._invites = {k: v for k, v in self._invites.items() if v.family_id != family_id}
        dropped_provider_ids = [
            pid for pid, p in self._providers.items() if p.family_id == family_id
        ]
        self._providers = {k: v for k, v in self._providers.items() if v.family_id != family_id}
        for pid in dropped_provider_ids:
            self._family_provider_api_key_ciphertext.pop(pid, None)
        self._vault_seeds.pop(family_id, None)
        # Mirror the vault seed: dropping the family therapist prompt here
        # too, so a partial wipe (e.g. an admin route that calls
        # ``wipe_family_scope`` without disband) leaves no prompt residue.
        self._therapist_prompts.pop(family_id, None)

    async def table_exists(self) -> bool:
        return True


# --- factory ---


def make_family_store(settings: Settings) -> FamilyStore:
    """Pick the family store by ``COMPANION_USE_DB``. Falls back to in-memory
    so the API never fails to boot."""
    if not settings.use_db:
        return InMemoryFamilyStore()
    return PostgresFamilyStore(settings)


# --- Postgres implementation ------------------------------------------------


class PostgresFamilyStore:
    """SQLAlchemy family store — used in ``docker compose`` (``COMPANION_USE_DB=1``).

    Shares the async engine from ``db.session``. ``table_exists()`` lets the
    lifespan hook fall back to in-memory when the family tables haven't been
    migrated yet, same pattern as the auth + memory stores.

    All methods are async, keyword-only, and mirror the in-memory store
    line-by-line. Read paths use ``select(...).where(...)``; write paths use
    ``s.add(model_row(...)) + await s.commit()``. The cross-family 404
    contract (CLAUDE.md) is enforced by filtering every read by ``family_id``
    (or ``user_id`` for ``get_family_for_user``) — the router layer adds the
    principal-membership check on top.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _session(self):  # type: ignore[no-untyped-def]
        from ..db.session import get_sessionmaker  # lazy: keep zero-config import path clean

        sm = get_sessionmaker(self._settings)
        return sm()

    async def table_exists(self) -> bool:
        from sqlalchemy import text  # lazy

        # Probe all 4 family tables — any missing table means the migration
        # partially applied and we should fall back to in-memory rather than
        # 500 on the first query.
        try:
            async with await self._session() as s:
                for tbl in ("families", "family_members", "family_invites", "family_providers"):
                    val = (await s.execute(text(f"SELECT to_regclass('public.{tbl}')"))).scalar()
                    if val is None:
                        logger.warning(
                            "family table %s missing — falling back to in-memory family store",
                            tbl,
                        )
                        return False
            return True
        except Exception:  # noqa: BLE001 — degrade gracefully, like make_store
            logger.warning(
                "family table probe failed — falling back to in-memory family store",
                exc_info=True,
            )
            return False

    # --- family CRUD ---

    async def create_family(self, *, name: str, owner_user_id: str) -> Family:
        from ..db import models as dbm  # lazy

        fid = uuid.uuid4().hex
        now = _utcnow()
        async with await self._session() as s:
            s.add(
                dbm.Family(
                    id=fid,
                    name=name,
                    owner_user_id=owner_user_id,
                    created_at=now,
                )
            )
            # Owner is materialized as a family_members row so list_members +
            # the recall-attribution lookup treat them uniformly — same wiring
            # as the in-memory store.
            s.add(
                dbm.FamilyMember(
                    family_id=fid,
                    user_id=owner_user_id,
                    family_role=FamilyRole.owner.value,
                    family_display_name="",
                    relation="other",
                    color="#7c3aed",
                    joined_at=now,
                )
            )
            await s.commit()
        return Family(id=fid, name=name, owner_user_id=owner_user_id, created_at=now)

    async def get_family_for_user(
        self, *, user_id: str, preferred_family_id: str | None = None
    ) -> Family | None:
        # See the in-memory impl for the rationale: the application-level
        # "one family per user" invariant lives in ``users.family_id``,
        # NOT in ``family_members``. When a user is a member of several
        # families (e.g. older memberships were never cleaned up after
        # disband), the caller's preferred family (the principal's
        # ``users.family_id``) wins — otherwise the LLM stream endpoint
        # 404s on the cross-family check.
        #
        # 1. If ``preferred_family_id`` is set AND the user is a member
        #    of that family, return it (cheap point lookup).
        # 2. Otherwise fall back to the most recent membership, ordered
        #    by ``joined_at DESC`` so the result is deterministic.
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            if preferred_family_id is not None:
                row = (
                    await s.execute(
                        select(dbm.Family)
                        .join(dbm.FamilyMember, dbm.FamilyMember.family_id == dbm.Family.id)
                        .where(
                            dbm.FamilyMember.user_id == user_id,
                            dbm.Family.id == preferred_family_id,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if row is not None:
                    return _row_to_family(row)
            row = (
                await s.execute(
                    select(dbm.Family)
                    .join(
                        dbm.FamilyMember,
                        dbm.FamilyMember.family_id == dbm.Family.id,
                    )
                    .where(dbm.FamilyMember.user_id == user_id)
                    .order_by(dbm.FamilyMember.joined_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_family(row) if row is not None else None

    async def get_family(self, *, family_id: str) -> Family | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(select(dbm.Family).where(dbm.Family.id == family_id).limit(1))
            ).scalar_one_or_none()
            return _row_to_family(row) if row is not None else None

    async def rename_family(self, *, family_id: str, name: str) -> Family | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(select(dbm.Family).where(dbm.Family.id == family_id).limit(1))
            ).scalar_one_or_none()
            if row is None:
                return None
            row.name = name
            await s.commit()
            await s.refresh(row)
            return _row_to_family(row)

    async def set_use_owner_personal_key(
        self, *, family_id: str, value: bool
    ) -> Family | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(select(dbm.Family).where(dbm.Family.id == family_id).limit(1))
            ).scalar_one_or_none()
            if row is None:
                return None
            row.use_owner_personal_key = value
            await s.commit()
            await s.refresh(row)
            return _row_to_family(row)

    async def disband_family(self, *, family_id: str) -> bool:
        from sqlalchemy import delete, select

        from ..db import models as dbm

        async with await self._session() as s:
            existing = (
                await s.execute(select(dbm.Family.id).where(dbm.Family.id == family_id).limit(1))
            ).scalar_one_or_none()
            if existing is None:
                return False
            # Drop dependents first, then the family row. wipe_family_scope
            # already clears family_providers / family_invites / family_members
            # for the memory store; here we do the same in raw SQL.
            await s.execute(
                delete(dbm.FamilyProvider).where(dbm.FamilyProvider.family_id == family_id)
            )
            await s.execute(delete(dbm.FamilyInvite).where(dbm.FamilyInvite.family_id == family_id))
            await s.execute(delete(dbm.FamilyMember).where(dbm.FamilyMember.family_id == family_id))
            # Wipe vault metadata on the family row (in-memory does this in
            # wipe_family_scope; for disband the row itself goes).
            await s.execute(delete(dbm.Family).where(dbm.Family.id == family_id))
            await s.commit()
            return True

    # --- members ---

    async def list_members(self, *, family_id: str) -> list[FamilyMember]:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(dbm.FamilyMember).where(dbm.FamilyMember.family_id == family_id)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_to_member(r) for r in rows]

    async def add_member(
        self,
        *,
        family_id: str,
        user_id: str,
        family_role: FamilyRole,
        family_display_name: str,
        relation: str,
        color: str,
    ) -> FamilyMember:
        # Upsert on (family_id, user_id): the owner row is already
        # materialized by ``create_family``, and ``create_family`` in
        # routers/family.py then calls ``add_member`` to attach the
        # display name. A bare INSERT would 409 on the composite PK; an
        # upsert keeps the call idempotent and lets the owner-row update
        # flow through without a separate code path.
        from sqlalchemy import text  # lazy

        from ..db import models as dbm

        joined_at = _utcnow()
        async with await self._session() as s:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO family_members ("
                        "  family_id, user_id, family_role, family_display_name,"
                        "  relation, color, joined_at"
                        ") VALUES ("
                        "  :fid, :uid, :role, :name, :rel, :color, :joined"
                        ") ON CONFLICT (family_id, user_id) DO UPDATE SET "
                        "  family_role = EXCLUDED.family_role, "
                        "  family_display_name = EXCLUDED.family_display_name, "
                        "  relation = EXCLUDED.relation, "
                        "  color = EXCLUDED.color "
                        "RETURNING *"
                    ),
                    {
                        "fid": family_id,
                        "uid": user_id,
                        "role": family_role.value,
                        "name": family_display_name,
                        "rel": relation,
                        "color": color,
                        "joined": joined_at,
                    },
                )
            ).first()
            await s.commit()
            return _row_to_member(_materialize_member_row(row, dbm))

    async def remove_member(self, *, family_id: str, user_id: str) -> bool:
        from sqlalchemy import delete

        from ..db import models as dbm

        async with await self._session() as s:
            result = await s.execute(
                delete(dbm.FamilyMember).where(
                    dbm.FamilyMember.family_id == family_id,
                    dbm.FamilyMember.user_id == user_id,
                )
            )
            await s.commit()
            return (result.rowcount or 0) > 0

    async def is_member(self, *, family_id: str, user_id: str) -> bool:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyMember.family_id).where(
                        dbm.FamilyMember.family_id == family_id,
                        dbm.FamilyMember.user_id == user_id,
                    )
                )
            ).first()
            return row is not None

    # --- invites ---

    async def create_invite(
        self,
        *,
        family_id: str,
        email: str,
        role: FamilyRole,
        token_hash: str,
        invited_by: str,
        expires_at: datetime,
    ) -> FamilyInvite:

        from ..db import models as dbm

        iid = uuid.uuid4().hex
        now = _utcnow()
        async with await self._session() as s:
            s.add(
                dbm.FamilyInvite(
                    id=iid,
                    family_id=family_id,
                    email=email.lower(),
                    role=role.value,
                    token_hash=token_hash,
                    invited_by=invited_by,
                    expires_at=expires_at,
                    accepted_at=None,
                    created_at=now,
                )
            )
            await s.commit()
        return FamilyInvite(
            id=iid,
            family_id=family_id,
            email=email.lower(),
            role=role,
            expires_at=expires_at,
            created_at=now,
            accepted_at=None,
            invited_by=invited_by,
        )

    async def list_invites(self, *, family_id: str) -> list[FamilyInvite]:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(dbm.FamilyInvite)
                        .where(dbm.FamilyInvite.family_id == family_id)
                        .order_by(dbm.FamilyInvite.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                FamilyInvite(
                    id=r.id,
                    family_id=r.family_id,
                    email=r.email,
                    role=FamilyRole(r.role),
                    expires_at=r.expires_at,
                    created_at=r.created_at,
                    accepted_at=r.accepted_at,
                    invited_by=r.invited_by,
                )
                for r in rows
            ]

    async def get_invite_by_hash(self, *, token_hash: str) -> FamilyInviteRecord | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyInvite)
                    .where(dbm.FamilyInvite.token_hash == token_hash)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_invite_record(row) if row is not None else None

    async def get_invite(self, *, invite_id: str) -> FamilyInviteRecord | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyInvite).where(dbm.FamilyInvite.id == invite_id).limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_invite_record(row) if row is not None else None

    async def mark_invite_accepted(self, *, invite_id: str) -> bool:
        from sqlalchemy import update

        from ..db import models as dbm

        now = _utcnow()
        async with await self._session() as s:
            result = await s.execute(
                update(dbm.FamilyInvite)
                .where(
                    dbm.FamilyInvite.id == invite_id,
                    dbm.FamilyInvite.accepted_at.is_(None),
                )
                .values(accepted_at=now)
            )
            await s.commit()
            return (result.rowcount or 0) > 0

    async def delete_invite(self, *, invite_id: str) -> bool:
        from sqlalchemy import delete

        from ..db import models as dbm

        async with await self._session() as s:
            result = await s.execute(
                delete(dbm.FamilyInvite).where(dbm.FamilyInvite.id == invite_id)
            )
            await s.commit()
            return (result.rowcount or 0) > 0

    async def find_pending_invite_for_email(
        self, *, family_id: str, email: str
    ) -> FamilyInviteRecord | None:
        from sqlalchemy import select

        from ..db import models as dbm

        e = email.lower()
        now = _utcnow()
        async with await self._session() as s:
            row = (
                (
                    await s.execute(
                        select(dbm.FamilyInvite)
                        .where(
                            dbm.FamilyInvite.family_id == family_id,
                            dbm.FamilyInvite.accepted_at.is_(None),
                            dbm.FamilyInvite.expires_at > now,
                        )
                        .order_by(dbm.FamilyInvite.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for r in row:
                if r.email.lower() == e:
                    return _row_to_invite_record(r)
            return None

    async def consume_pending_invite_for_email(self, *, email: str) -> FamilyInviteRecord | None:
        """Same as ``find_pending_invite_for_email`` but unscoped — used by the
        magiclink auto-attach path that can match any family the email has a
        pending invite for."""
        from sqlalchemy import select

        from ..db import models as dbm

        e = email.lower()
        now = _utcnow()
        async with await self._session() as s:
            row = (
                (
                    await s.execute(
                        select(dbm.FamilyInvite)
                        .where(
                            dbm.FamilyInvite.accepted_at.is_(None),
                            dbm.FamilyInvite.expires_at > now,
                        )
                        .order_by(dbm.FamilyInvite.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            for r in row:
                if r.email.lower() == e:
                    return _row_to_invite_record(r)
            return None

    # --- single-use consumed tokens (post-MVP hardening) ---
    async def consume_invite_token(self, *, token_hash: str) -> bool:
        """Atomic INSERT...ON CONFLICT DO NOTHING on ``consumed_tokens``. True
        on first insert, False on replay. The PK collision is the canonical
        "already used" signal — no separate status column needed."""
        from sqlalchemy import text  # lazy

        async with await self._session() as s:
            row = (
                await s.execute(
                    text(
                        "INSERT INTO consumed_tokens (token_hash, kind) "
                        "VALUES (:h, 'family_invite') "
                        "ON CONFLICT (token_hash) DO NOTHING "
                        "RETURNING token_hash"
                    ),
                    {"h": token_hash},
                )
            ).first()
            await s.commit()
            return row is not None

    # --- vault seed ---

    async def get_vault_seed(self, *, family_id: str) -> _VaultSeed:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.Family.family_salt, dbm.Family.family_enc_blob_seed).where(
                        dbm.Family.id == family_id
                    )
                )
            ).first()
            if row is None:
                return _VaultSeed()
            return _VaultSeed(
                family_salt=row.family_salt,
                family_enc_blob_seed=row.family_enc_blob_seed,
            )

    async def set_vault_seed(
        self,
        *,
        family_id: str,
        family_salt: str | None,
        family_enc_blob_seed: str | None,
    ) -> None:
        from sqlalchemy import update

        from ..db import models as dbm

        async with await self._session() as s:
            await s.execute(
                update(dbm.Family)
                .where(dbm.Family.id == family_id)
                .values(family_salt=family_salt, family_enc_blob_seed=family_enc_blob_seed)
            )
            await s.commit()

    # --- therapist prompt ---

    async def get_therapist_prompt(self, *, family_id: str) -> _TherapistPrompt:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(
                        dbm.Family.therapist_prompt,
                        dbm.Family.therapist_prompt_set_by,
                        dbm.Family.therapist_prompt_set_at,
                    ).where(dbm.Family.id == family_id)
                )
            ).first()
            if row is None:
                # Unknown family — match the in-memory default so the router
                # (which already gated on _require_member) returns a clean
                # null body to a member of a family that has been hard-deleted
                # between two requests.
                return _TherapistPrompt()
            return _TherapistPrompt(
                body=row.therapist_prompt,
                set_by_user_id=row.therapist_prompt_set_by,
                set_at=row.therapist_prompt_set_at,
            )

    async def set_therapist_prompt(
        self,
        *,
        family_id: str,
        body: str | None,
        set_by_user_id: str,
    ) -> _TherapistPrompt:
        # ``RETURNING *`` so the server-side timestamp (DB clock) is the
        # authoritative ``set_at`` — we never trust the caller's wall clock.
        # An explicit ``set_at = NULL`` would break the audit; a clear
        # (body=None) keeps the audit fields so the UI can show
        # "Set by <name> · <date> — reset to built-in". The router issues
        # body=None as a separate "clear" call (writes the audit row but
        # body=None).
        from sqlalchemy import text  # lazy

        async with await self._session() as s:
            row = (
                await s.execute(
                    text(
                        "UPDATE families SET "
                        "  therapist_prompt = :body, "
                        "  therapist_prompt_set_by = :setter, "
                        "  therapist_prompt_set_at = NOW() "
                        "WHERE id = :fid "
                        "RETURNING therapist_prompt, therapist_prompt_set_by, "
                        "          therapist_prompt_set_at"
                    ),
                    {"body": body, "setter": set_by_user_id, "fid": family_id},
                )
            ).first()
            await s.commit()
            if row is None:
                # The family vanished between the router's _require_owner and
                # the UPDATE. Surface as a 404 from the caller — the wire
                # contract is "no cross-family / no cross-tenant".
                return _TherapistPrompt()
            return _TherapistPrompt(
                body=row.therapist_prompt,
                set_by_user_id=row.therapist_prompt_set_by,
                set_at=row.therapist_prompt_set_at,
            )

    # --- family providers ---

    async def add_family_provider(
        self, *, p: FamilyProvider, api_key_ciphertext: str | None = None
    ) -> FamilyProvider:
        from ..db import models as dbm  # lazy

        async with await self._session() as s:
            s.add(
                dbm.FamilyProvider(
                    id=p.id,
                    family_id=p.family_id,
                    kind=p.kind.value,
                    label=p.label,
                    base_url=p.base_url,
                    key_handle=p.key_handle,
                    model=p.model,
                    embeddings_model=p.embeddings_model,
                    enc_blob=p.enc_blob,
                    api_key_ciphertext=api_key_ciphertext,
                    created_at=p.created_at,
                )
            )
            await s.commit()
        return p

    async def list_family_providers(self, *, family_id: str) -> list[FamilyProvider]:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(dbm.FamilyProvider)
                        .where(dbm.FamilyProvider.family_id == family_id)
                        .order_by(dbm.FamilyProvider.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_to_provider(r) for r in rows]

    async def get_family_provider(
        self, *, family_id: str, provider_id: str
    ) -> FamilyProvider | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyProvider)
                    .where(
                        dbm.FamilyProvider.id == provider_id,
                        dbm.FamilyProvider.family_id == family_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return _row_to_provider(row) if row is not None else None

    async def get_family_provider_api_key_ciphertext(
        self, *, family_id: str, key_handle: str
    ) -> str | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyProvider.api_key_ciphertext)
                    .where(
                        dbm.FamilyProvider.family_id == family_id,
                        dbm.FamilyProvider.key_handle == key_handle,
                    )
                    .order_by(dbm.FamilyProvider.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return row if row is not None else None

    async def update_family_provider(
        self,
        *,
        family_id: str,
        provider_id: str,
        label: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        embeddings_model: str | None = None,
    ) -> FamilyProvider | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyProvider)
                    .where(
                        dbm.FamilyProvider.id == provider_id,
                        dbm.FamilyProvider.family_id == family_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if label is not None:
                row.label = label
            if base_url is not None:
                row.base_url = base_url
            if model is not None:
                row.model = model
            if embeddings_model is not None:
                # None = keep; "" = clear (family semantic memory off).
                row.embeddings_model = embeddings_model or None
            await s.commit()
            await s.refresh(row)
            return _row_to_provider(row)

    async def set_family_provider_enc_blob(
        self,
        *,
        family_id: str,
        provider_id: str,
        enc_blob: str | None = None,
        api_key_ciphertext: str | None = None,
    ) -> FamilyProvider | None:
        from sqlalchemy import select

        from ..db import models as dbm

        async with await self._session() as s:
            row = (
                await s.execute(
                    select(dbm.FamilyProvider)
                    .where(
                        dbm.FamilyProvider.id == provider_id,
                        dbm.FamilyProvider.family_id == family_id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if enc_blob is not None:
                row.enc_blob = enc_blob
            if api_key_ciphertext is not None:
                row.api_key_ciphertext = api_key_ciphertext
            await s.commit()
            await s.refresh(row)
            return _row_to_provider(row)

    async def delete_family_provider(self, *, family_id: str, provider_id: str) -> bool:
        from sqlalchemy import delete

        from ..db import models as dbm

        async with await self._session() as s:
            result = await s.execute(
                delete(dbm.FamilyProvider).where(
                    dbm.FamilyProvider.id == provider_id,
                    dbm.FamilyProvider.family_id == family_id,
                )
            )
            await s.commit()
            return (result.rowcount or 0) > 0

    # --- scope wipes ---

    async def wipe_member_in_family(self, *, family_id: str, user_id: str) -> None:
        from sqlalchemy import delete

        from ..db import models as dbm

        async with await self._session() as s:
            await s.execute(
                delete(dbm.FamilyMember).where(
                    dbm.FamilyMember.family_id == family_id,
                    dbm.FamilyMember.user_id == user_id,
                )
            )
            await s.commit()

    async def wipe_family_scope(self, *, family_id: str) -> None:
        """Same as in-memory: drop providers + invites + members; null the
        vault seed + therapist prompt on the family row (the family row
        itself is removed by ``disband_family``)."""
        from sqlalchemy import delete, update

        from ..db import models as dbm

        async with await self._session() as s:
            await s.execute(
                delete(dbm.FamilyProvider).where(dbm.FamilyProvider.family_id == family_id)
            )
            await s.execute(delete(dbm.FamilyInvite).where(dbm.FamilyInvite.family_id == family_id))
            await s.execute(delete(dbm.FamilyMember).where(dbm.FamilyMember.family_id == family_id))
            await s.execute(
                update(dbm.Family)
                .where(dbm.Family.id == family_id)
                .values(
                    family_salt=None,
                    family_enc_blob_seed=None,
                    # Mirror the vault seed: null the prompt + audit so a
                    # partial wipe leaves no author attribution pointing at
                    # a family the user no longer belongs to.
                    therapist_prompt=None,
                    therapist_prompt_set_by=None,
                    therapist_prompt_set_at=None,
                )
            )
            await s.commit()


# --- row mappers -------------------------------------------------------------


def _row_to_family(row) -> Family:  # type: ignore[no-untyped-def]
    return Family(
        id=row.id,
        name=row.name,
        owner_user_id=row.owner_user_id,
        created_at=row.created_at,
        use_owner_personal_key=bool(getattr(row, "use_owner_personal_key", False)),
    )


def _row_to_member(row) -> FamilyMember:  # type: ignore[no-untyped-def]
    return FamilyMember(
        family_id=row.family_id,
        user_id=row.user_id,
        family_role=FamilyRole(row.family_role),
        family_display_name=row.family_display_name,
        relation=row.relation,
        color=row.color,
        joined_at=row.joined_at,
    )


def _row_to_invite(row) -> FamilyInvite:  # type: ignore[no-untyped-def]
    """Wire shape — drops ``token_hash`` (server-only)."""
    return FamilyInvite(
        id=row.id,
        family_id=row.family_id,
        email=row.email,
        role=FamilyRole(row.role),
        expires_at=row.expires_at,
        created_at=row.created_at,
        accepted_at=row.accepted_at,
        invited_by=row.invited_by,
    )


def _row_to_invite_record(row) -> FamilyInviteRecord:  # type: ignore[no-untyped-def]
    """Internal record — keeps ``token_hash`` (for ``get_invite_by_hash`` /
    accept-token replay protection)."""
    return FamilyInviteRecord(
        id=row.id,
        family_id=row.family_id,
        email=row.email,
        role=row.role,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        invited_by=row.invited_by,
        created_at=row.created_at,
    )


def _row_to_provider(row) -> FamilyProvider:  # type: ignore[no-untyped-def]
    return FamilyProvider(
        id=row.id,
        family_id=row.family_id,
        kind=ProviderKind(row.kind),
        label=row.label,
        base_url=row.base_url,
        key_handle=row.key_handle,
        model=row.model,
        embeddings_model=getattr(row, "embeddings_model", None),
        enc_blob=row.enc_blob,
        created_at=row.created_at,
    )


def _materialize_member_row(row, dbm):  # type: ignore[no-untyped-def]
    """``add_member`` uses raw SQL ``ON CONFLICT ... RETURNING *`` (the
    simplest way to express an upsert with the same RETURNING shape on
    both insert and update). The returned ``row`` is a SQLAlchemy ``Row``
    keyed by column name — wrap it in a transient ``FamilyMember`` so
    ``_row_to_member`` reads it the same way as an ORM-mapped instance."""
    return dbm.FamilyMember(
        family_id=row.family_id,
        user_id=row.user_id,
        family_role=row.family_role,
        family_display_name=row.family_display_name,
        relation=row.relation,
        color=row.color,
        joined_at=row.joined_at,
    )


__all__ = [
    "FamilyStore",
    "FamilyStoreError",
    "InMemoryFamilyStore",
    "PostgresFamilyStore",
    "make_family_store",
]
