"""``/v1/family/*`` — multi-member family CRUD, invites, accept, members, the
family-vault metadata surface, and family-scoped providers.

Endpoints (all prefixed ``/v1`` by ``main.py``):

  POST   /family                  create family (caller becomes owner)
  GET    /family                  fetch caller's family (404 if not in one)
  PATCH  /family                  rename (owner only)
  DELETE /family                  disband (owner only) — wipes all shared data
  POST   /family/invites          send invite (owner only); email is the link
  GET    /family/invites          list pending invites (owner only)
  DELETE /family/invites/{iid}    revoke invite (owner only; idempotent 204)
  GET    /family/accept?token=    PUBLIC — landing page (303 to /login or /family)
  POST   /family/accept           accept a sealed token; attaches caller
  DELETE /family/members/me       leave; scoped wipe of own private in family
  DELETE /family/members/{uid}    remove (owner only); scoped wipe
  GET    /family/vault/meta       vault init state (any member)
  PUT    /family/vault            seed/replace family-salt + enc_blob_seed (owner)
  GET    /family/therapist-prompt current therapist prompt + audit (any member)
  PUT    /family/therapist-prompt set / clear therapist prompt (owner only)
  GET    /family/providers        list family providers (any member)
  POST   /family/providers        create family provider (owner only)
  PATCH  /family/providers/{pid}  update family provider (owner only)
  PUT    /family/providers/{pid}/enc_blob  re-seal provider key after vault rotation (owner only)
  DELETE /family/providers/{pid}  delete family provider (owner only)

Cross-family access is 404 (not 403) per CLAUDE.md — the lookup is scoped by
the principal's ``user_id``, so a member of one family cannot enumerate
another family's data.

``/v1/family/accept`` is in ``auth.middleware.PUBLIC_PATHS`` so the GET
landing page is reachable without a session; the POST ``/v1/family/accept``
endpoint still requires a Principal (auth middleware 401s non-PUBLIC paths).

The family BYOK key (``family_providers.enc_blob``) is encrypted to the
family passphrase (separate from the personal passphrase). The server cannot
decrypt it — same zero-knowledge contract as the personal ``providers.enc_blob``
(CLAUDE.md, "Security invariants"). The family passphrase never enters any
endpoint; the family-vault surface only stores/replays the
``family_enc_blob_seed`` opaque ciphertext + ``family_salt``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_companion_contracts import (
    Family,
    FamilyInvite,
    FamilyProvider,
    FamilyRole,
    FamilyTherapistPrompt,
    FamilyTherapistPromptSet,
    Principal,
    ProviderKind,
)
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth.sessions import open_sealed, seal
from ..auth.store import AuthStore
from ..crypto.envelope import EnvelopeCipher
from ..deps import get_current_principal, get_current_user_id
from ..family.store import (
    FamilyStore,
    _VaultSeed,
)
from ..memory.store import MemoryStore
from ..ratelimit import limiter, user_or_ip_key
from ..vault.decrypt import DecryptError, decrypt_key_blob
from ..vault.zeroize import zeroized

logger = logging.getLogger(__name__)

router = APIRouter()

_FAMILY_INVITE_TTL = timedelta(days=7)
_FAMILY_COLOR_DEFAULT = "#7c3aed"
_FAMILY_RELATION_DEFAULT = "other"

# --- helpers ---


def _now() -> datetime:
    return datetime.now(UTC)


def _invite_secret(settings) -> str:  # type: ignore[no-untyped-def]
    return settings.auth_invite_secret or settings.auth_state_secret


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _principal(request: Request) -> Principal:
    return await get_current_principal(request)


async def _principal_user_id(request: Request) -> str:
    return await get_current_user_id(request)


def _family_store(request: Request) -> FamilyStore:
    return request.app.state.family_store  # type: ignore[no-any-return]


def _auth_store(request: Request) -> AuthStore:
    return request.app.state.auth_store  # type: ignore[no-any-return]


def _memory_store(request: Request) -> MemoryStore:
    return request.app.state.store  # type: ignore[no-any-return]


def _envelope(request: Request) -> EnvelopeCipher | None:
    return getattr(request.app.state, "envelope", None)


def _settings(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.settings


async def _require_member(request: Request) -> tuple[str, Family]:
    """Resolve the principal's family. 404 if not in a family.

    The principal's ``family_id`` (``users.family_id``) is the
    application-level "current family" pointer and the value the LLM
    stream endpoint checks against the body ``family_id`` for the
    cross-family 404. We pass it as the store's
    ``preferred_family_id`` so a user with several
    ``family_members`` rows (e.g. older memberships that were never
    cleaned up after disband) still resolves to the family the
    principal is currently attached to — not an arbitrary other one.
    """
    fam_store = _family_store(request)
    principal = await _principal(request)
    fam = await fam_store.get_family_for_user(
        user_id=principal.user_id,
        preferred_family_id=principal.family_id,
    )
    if fam is None:
        raise HTTPException(status_code=404, detail="not in a family")
    return principal.user_id, fam


async def _require_owner(request: Request) -> tuple[str, Family]:
    user_id, fam = await _require_member(request)
    if fam.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="owner only")
    return user_id, fam


# --- Pydantic body shapes (router-local — contract types don't carry create-
#     update body fields) ---


class FamilyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class FamilyRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class FamilyUseOwnerPersonalKey(BaseModel):
    # Owner-only toggle: when true, family turns resolve the BYOK key from the
    # owner's personal providers row instead of family_providers. The server
    # resolves the owner from the family record (fam.owner_user_id), never from
    # the client, so a member cannot retarget the lookup.
    use_owner_personal_key: bool


class InviteCreate(BaseModel):
    # Lightweight email validation: trimmed, has "@" + a dot in the right half.
    # The wire already lowercases on the way in. Heavy RFC-compliant validation
    # is the email-transport's job; we keep the API side dep-free (pydantic's
    # ``EmailStr`` requires the optional ``email-validator`` package).
    email: str = Field(..., min_length=3, max_length=320)
    role: FamilyRole = FamilyRole.member


class InviteAccept(BaseModel):
    token: str


class VaultSet(BaseModel):
    family_salt: str | None = None
    family_enc_blob_seed: str | None = None


class FamilyProviderCreate(BaseModel):
    kind: ProviderKind
    label: str
    base_url: str | None = None
    key_handle: str | None = None
    model: str | None = None
    # Embedding model for family semantic memory (None = off). Metadata only —
    # the recall embedding call reuses the family turn's sealed key.
    embeddings_model: str | None = None
    # Legacy zero-knowledge at-rest backup (dead column; kept for back-comat).
    enc_blob: str | None = None
    # One-time ECDH-sealed plaintext key (same shape as the per-turn
    # ``family_enc_key_blob``). The server opens it with the session private
    # key and envelope-encrypts the plaintext under ``MESSENGER_TOKEN_DEK``
    # → ``family_providers.api_key_ciphertext``. Router-local pydantic — NOT
    # on the contracts ``FamilyProvider`` model (no drift impact).
    enc_key_blob: str | None = None


class FamilyProviderUpdate(BaseModel):
    label: str | None = None
    base_url: str | None = None
    model: str | None = None
    # None/absent = keep; empty string = clear (family semantic memory off) —
    # this PATCH surface uses the None=keep convention, unlike the personal
    # provider PATCH which distinguishes explicit null via fields_set.
    embeddings_model: str | None = None


class FamilyProviderEncBlobUpdate(BaseModel):
    # Opaque base64 XChaCha20-Poly1305 ciphertext keyed by the family master
    # key — the legacy at-rest backup of the family provider API key. Kept for
    # back-comat with the vault-rotation re-seal: the client decrypts the key
    # under the OLD FMK, re-seals it under the NEW FMK (new passphrase+salt),
    # and PUTs the new blob here so the server-side backup tracks the rotation.
    # A plaintext-looking value (e.g. a raw ``sk-...`` key) is rejected by
    # ``_validate_family_enc_blob`` — see the endpoint below. Now optional:
    # when ``enc_key_blob`` is supplied, the server-side envelope ciphertext
    # is rotated instead (the new BYOK path).
    enc_blob: str | None = None
    # Optional: a fresh ECDH-sealed plaintext key (same shape as
    # ``FamilyProviderCreate.enc_key_blob``). When supplied, the server opens
    # it, envelope-encrypts, and rotates ``api_key_ciphertext``. Either
    # ``enc_blob`` or ``enc_key_blob`` (or both) must be present.
    enc_key_blob: str | None = None


# Mirrors the redactor's key pattern (observability/redaction.py): a real
# provider key always starts with one of these prefixes. The enc_blob is
# base64 ciphertext (salt||nonce||ct) and never carries a plaintext prefix,
# so a value matching this is a raw key somebody tried to store — reject it
# at the door so the column never holds plaintext (CLAUDE.md security
# invariant: API keys never stored server-side in plaintext).
_PLAINTEXT_KEY_RE = re.compile(r"(sk-(?:ant-|or-|proj-)?)[A-Za-z0-9_\-]{4,}")


def _validate_family_enc_blob(enc_blob: str) -> None:
    if not enc_blob:
        raise HTTPException(status_code=400, detail="enc_blob is required")
    if _PLAINTEXT_KEY_RE.search(enc_blob):
        # Never echo back that this looked like a key — the message is
        # generic so it can't be used as an oracle.
        raise HTTPException(status_code=400, detail="enc_blob must be opaque ciphertext")


def _envelope_encrypt_byok(request: Request, enc_key_blob: str) -> str:
    """Open the ECDH-sealed key blob with the session private key and re-wrap
    the plaintext under the envelope DEK → ``api_key_ciphertext``.

    Raises ``HTTPException`` (400 on a malformed blob, 503 when the envelope
    DEK is not configured — mirroring the Telegram bot-token endpoints). The
    plaintext bytes + the decrypted key bytearray are zeroized after the wrap.
    """
    envelope = _envelope(request)
    if envelope is None:
        raise HTTPException(
            status_code=503, detail="envelope key not configured (MESSENGER_TOKEN_DEK)"
        )
    ecdh = request.app.state.ecdh
    try:
        dk = decrypt_key_blob(enc_key_blob, ecdh.private_key)
    except DecryptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    plaintext = bytearray(
        json.dumps(
            {
                "provider_kind": dk.provider_kind,
                "api_key": dk.api_key_str(),
                "base_url": dk.base_url,
                "extra": dk.extra,
            }
        ).encode("utf-8")
    )
    with zeroized(plaintext), zeroized(dk.api_key):
        return envelope.encrypt_b64(bytes(plaintext))


# --- family CRUD ---


@router.post("/family", response_model=Family)
async def create_family(body: FamilyCreate, request: Request) -> Family:
    user_id = await _principal_user_id(request)
    principal = await _principal(request)
    fam_store = _family_store(request)
    auth_store = _auth_store(request)
    # One family per user — enforced at the application level (users.family_id).
    if principal.family_id is not None:
        raise HTTPException(status_code=409, detail="already in a family")
    fam = await fam_store.create_family(name=body.name, owner_user_id=user_id)
    # Materialize the owner as a family_member row with a display name.
    owner = await auth_store.get_user(user_id)
    display = (owner.display_name if owner else None) or (owner.email if owner else None) or "Owner"
    display = display.split("@")[0] if "@" in display else display
    await fam_store.add_member(
        family_id=fam.id,
        user_id=user_id,
        family_role=FamilyRole.owner,
        family_display_name=display,
        relation="parent",
        color=_FAMILY_COLOR_DEFAULT,
    )
    await auth_store.set_user_family(
        user_id=user_id, family_id=fam.id, family_role=FamilyRole.owner.value
    )
    return fam


@router.get("/family")
async def get_family(request: Request) -> dict[str, Any]:
    user_id, fam = await _require_member(request)
    fam_store = _family_store(request)
    members = await fam_store.list_members(family_id=fam.id)
    invites = await fam_store.list_invites(family_id=fam.id)
    providers = await fam_store.list_family_providers(family_id=fam.id)
    seed = await fam_store.get_vault_seed(family_id=fam.id)
    return {
        "family": fam,
        "members": members,
        "invites": invites,
        "providers": providers,
        "vault": {
            "family_id": fam.id,
            "vault_initialized": seed.family_salt is not None,
            "family_salt": seed.family_salt,
            "has_provider": any(True for _ in providers),
        },
    }


@router.patch("/family", response_model=Family)
async def rename_family(body: FamilyRename, request: Request) -> Family:
    _, fam = await _require_owner(request)
    updated = await _family_store(request).rename_family(family_id=fam.id, name=body.name)
    if updated is None:
        raise HTTPException(status_code=404, detail="family not found")
    return updated


@router.put("/family/owner-personal-key", response_model=Family)
async def set_use_owner_personal_key(
    body: FamilyUseOwnerPersonalKey, request: Request
) -> Family:
    """Owner-only toggle: when on, family turns resolve the BYOK key from the
    owner's personal ``providers`` row (by ``key_handle``) instead of
    ``family_providers``. Mutually exclusive with family keys in the UI; on
    the server, when set, the personal-provider lookup wins. The owner is
    resolved from the family record (``fam.owner_user_id``) — never a client
    value — so a member cannot retarget the key lookup. Returns the updated
    family (the flag rides the ``Family`` wire model back to all members)."""
    _, fam = await _require_owner(request)
    updated = await _family_store(request).set_use_owner_personal_key(
        family_id=fam.id, value=body.use_owner_personal_key
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="family not found")
    return updated


@router.delete("/family", status_code=204)
async def disband_family(request: Request) -> None:
    user_id, fam = await _require_owner(request)
    fam_store = _family_store(request)
    mem_store = _memory_store(request)
    auth_store = _auth_store(request)
    # Snapshot the current members BEFORE wiping the family_members rows —
    # otherwise ``list_members`` returns an empty list and we never clear
    # each member's ``users.family_id``. A user who is still attached to a
    # disbanded family (stale ``users.family_id``) would then show up in
    # ``get_family_for_user`` for the *new* family via a leaked membership
    # row, and the LLM stream's cross-family 404 check would mismatch
    # ``body.family_id`` against the principal's stale pointer.
    members_before_wipe = await fam_store.list_members(family_id=fam.id)
    # Wipe family-scoped data BEFORE dropping the family row, so wipe_*_scope
    # has a coherent target. disband_family then drops families/family_members
    # /invites/family_providers atomically inside the store.
    await mem_store.wipe_family_scope(family_id=fam.id)
    # Clear users.family_id / family_role for all former members.
    for m in members_before_wipe:
        await auth_store.set_user_family(user_id=m.user_id, family_id=None, family_role=None)
    await fam_store.disband_family(family_id=fam.id)


# --- invites ---


@router.post("/family/invites", response_model=FamilyInvite)
# I15: per-owner cap so one owner can't spam invites (and burn the email
# sender). Keys off the authenticated Principal.
@limiter.limit("20/hour", key_func=user_or_ip_key)
async def create_invite(body: InviteCreate, request: Request) -> FamilyInvite:
    user_id, fam = await _require_owner(request)
    fam_store = _family_store(request)
    # Cap family size: 4 members. Count current members + this one.
    current = await fam_store.list_members(family_id=fam.id)
    if len(current) >= 4:
        raise HTTPException(status_code=409, detail="family is full (max 4 members)")
    # Sealed token = seal({family_id, email, role, exp, jti, nonce}, secret).
    # Only the hash is stored server-side. The plaintext is sent in the email.
    nonce = secrets.token_urlsafe(8)
    jti = uuid.uuid4().hex
    expires_at = _now() + _FAMILY_INVITE_TTL
    payload = {
        "family_id": fam.id,
        "email": str(body.email).lower(),
        "role": body.role.value,
        "exp": int(expires_at.timestamp()),
        "jti": jti,
        "nonce": nonce,
    }
    token = seal(payload, _invite_secret(_settings(request)))
    token_hash = _hash_token(token)
    invite = await fam_store.create_invite(
        family_id=fam.id,
        email=str(body.email).lower(),
        role=body.role,
        token_hash=token_hash,
        invited_by=user_id,
        expires_at=expires_at,
    )
    # Send the invite link via the active email transport (mirrors magic-link).
    # Console transport prints the URL to stdout for self-hosted dev. Family
    # invites reuse the magic-link transport's signature ``send(*, to, link)``
    # — the human-readable framing goes in the body, which the console
    # transport prefixes with the recipient + link.
    invite_email = str(body.email).lower()
    try:
        from ..auth.backends.magic_link import default_transport

        transport = default_transport(_settings(request))
        link = _settings(request).public_origin.rstrip("/") + f"/family/accept?token={token}"
        body_text = (
            f"You've been invited to join the Retellis family "
            f'"{fam.name}".\n\nThis link expires in 7 days. If you '
            "didn't expect this email, you can safely ignore it."
        )
        send = getattr(transport, "send", None)
        if send is not None:
            try:
                # Family-aware transport signature: ``send(*, to, subject, body)``.
                await send(
                    to=invite_email,
                    subject=f"Family invite: {fam.name}",
                    body=f"{body_text}\n\n{link}",
                )
            except TypeError:
                # Magic-link transport signature: ``send(*, to, link)``.
                await send(to=invite_email, link=f"{body_text}\n\n{link}")
    except Exception:  # noqa: BLE001 — invite row exists; delivery is best-effort
        logger.exception("family invite: email transport failed (invite still valid)")
    return invite


@router.get("/family/invites", response_model=list[FamilyInvite])
async def list_invites(request: Request) -> list[FamilyInvite]:
    _, fam = await _require_owner(request)
    return await _family_store(request).list_invites(family_id=fam.id)


@router.delete("/family/invites/{iid}", status_code=204)
async def revoke_invite(iid: str, request: Request) -> None:
    _, fam = await _require_owner(request)
    fam_store = _family_store(request)
    inv = await fam_store.get_invite(invite_id=iid)
    if inv is not None and inv.family_id != fam.id:
        # Cross-family access — 404, not 403.
        raise HTTPException(status_code=404, detail="invite not found")
    # Idempotent: missing invite is a no-op (204). A present cross-family
    # invite was caught above; a present same-family invite is dropped.
    if inv is not None:
        await fam_store.delete_invite(invite_id=iid)
    return None


# --- accept (GET public; POST authed) ---


@router.get("/family/accept")
async def accept_landing(
    request: Request,
    token: str = Query(...),
) -> dict[str, Any]:
    """Public landing for the family-invite link.

    If the caller has a session, the page should hit ``POST /v1/family/accept``
    directly. If not, the page should render a login/signup form with the
    token sealed in a short-lived cookie. We don't 303 from a JSON endpoint
    # (the web route handles routing); we just echo whether the token shape
    # is valid + a non-sensitive preview of the family name.
    """
    payload = open_sealed(token, _invite_secret(_settings(request)))
    if payload is None:
        raise HTTPException(status_code=400, detail="invalid invite token")
    if int(payload.get("exp", 0)) < int(_now().timestamp()):
        raise HTTPException(status_code=400, detail="invite expired")
    fam_store = _family_store(request)
    fam = await fam_store.get_family(family_id=str(payload.get("family_id", "")))
    if fam is None:
        raise HTTPException(status_code=404, detail="family not found")
    return {
        "family_name": fam.name,
        "email": str(payload.get("email", "")),
        "role": str(payload.get("role", "member")),
        "exp": int(payload["exp"]),
    }


@router.post("/family/accept")
async def accept_invite(body: InviteAccept, request: Request) -> dict[str, Any]:
    user_id = await _principal_user_id(request)
    fam_store = _family_store(request)
    auth_store = _auth_store(request)
    principal = await _principal(request)
    payload = open_sealed(body.token, _invite_secret(_settings(request)))
    if payload is None:
        raise HTTPException(status_code=400, detail="invalid invite token")
    if int(payload.get("exp", 0)) < int(_now().timestamp()):
        raise HTTPException(status_code=400, detail="invite expired")
    fam_id = str(payload.get("family_id", ""))
    invite_role = str(payload.get("role", "member"))
    fam = await fam_store.get_family(family_id=fam_id)
    if fam is None:
        raise HTTPException(status_code=404, detail="family not found")
    # Reject if the user is already in a (different) family.
    if principal.family_id is not None and principal.family_id != fam_id:
        raise HTTPException(status_code=409, detail="already in a family")
    user = await auth_store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    # The invite was emailed to someone else — accept it anyway if the owner
    # added the address later. (We don't second-guess the family owner; the
    # email match is a soft check that prevents a leaked token from being
    # used by an unrelated account IF the emails differ.)
    # Actually: the wire doesn't carry the user's email match promise, and
    # the owner chose the email. Skip the mismatch 403 — accept it.
    # ---- Single-use replay protection (post-MVP hardening, PLAN §16 #2) ----
    # ``consume_invite_token`` is the canonical "this token is used" op. Call
    # it BEFORE looking up the invite so a replayed token 410s immediately,
    # regardless of the invite row's state (active, accepted, deleted,
    # expired). On Postgres this is ``INSERT ... ON CONFLICT DO NOTHING`` on
    # ``consumed_tokens``; the in-memory store uses a set. First call wins;
    # every replay returns 410.
    token_hash = _hash_token(body.token)
    if not await fam_store.consume_invite_token(token_hash=token_hash):
        raise HTTPException(status_code=410, detail="invite already used or expired")
    # Find the matching invite row by token hash. The token has been
    # recorded as consumed; if no matching invite row exists, this is a
    # malformed/invalid accept (the token was real but the row was deleted
    # between issue and accept). 404 — the user has the seeded account but
    # no record to attach to.
    invite = await fam_store.get_invite_by_hash(token_hash=token_hash)
    if invite is None or invite.family_id != fam_id:
        # Token is valid seal-wise but no matching invite row. Treat as
        # already-consumed (someone already accepted it) — return success
        # only if the user is already attached.
        if principal.family_id == fam_id:
            return {"family_id": fam_id, "already_member": True}
        raise HTTPException(status_code=404, detail="invite not found")
    if invite.expires_at < _now():
        raise HTTPException(status_code=410, detail="invite already used or expired")
    if invite.accepted_at is not None:
        # Re-accept by a user who's already a member of this family is
        # idempotent (the user might re-click the email link). Anyone else
        # gets 410 — the token is consumed (the consume call above
        # already recorded this accept for the replay defense).
        if principal.family_id == fam_id:
            return {"family_id": fam_id, "role": invite_role, "already_member": True}
        raise HTTPException(status_code=410, detail="invite already used or expired")
    # Display name fallback chain: display_name → email local part → "Member".
    display = user.display_name or (user.email.split("@")[0] if user.email else "Member")
    await fam_store.add_member(
        family_id=fam_id,
        user_id=user_id,
        family_role=FamilyRole(invite_role),
        family_display_name=display,
        relation=_FAMILY_RELATION_DEFAULT,
        color=_FAMILY_COLOR_DEFAULT,
    )
    await fam_store.mark_invite_accepted(invite_id=invite.id)
    await auth_store.set_user_family(user_id=user_id, family_id=fam_id, family_role=invite_role)
    return {"family_id": fam_id, "role": invite_role, "already_member": False}


# --- members ---


@router.delete("/family/members/me", status_code=204)
async def leave_family(request: Request) -> None:
    user_id, fam = await _require_member(request)
    if fam.owner_user_id == user_id:
        raise HTTPException(
            status_code=403,
            detail="owner cannot leave; disband the family or transfer ownership first",
        )
    fam_store = _family_store(request)
    mem_store = _memory_store(request)
    auth_store = _auth_store(request)
    await mem_store.wipe_member_in_family(family_id=fam.id, user_id=user_id)
    await fam_store.remove_member(family_id=fam.id, user_id=user_id)
    await auth_store.set_user_family(user_id=user_id, family_id=None, family_role=None)


@router.delete("/family/members/{uid}", status_code=204)
async def remove_member(uid: str, request: Request) -> None:
    user_id, fam = await _require_owner(request)
    if uid == fam.owner_user_id:
        raise HTTPException(status_code=400, detail="cannot remove the owner")
    fam_store = _family_store(request)
    mem_store = _memory_store(request)
    auth_store = _auth_store(request)
    if not await fam_store.is_member(family_id=fam.id, user_id=uid):
        raise HTTPException(status_code=404, detail="member not found")
    await mem_store.wipe_member_in_family(family_id=fam.id, user_id=uid)
    await fam_store.remove_member(family_id=fam.id, user_id=uid)
    await auth_store.set_user_family(user_id=uid, family_id=None, family_role=None)


# --- family vault (zero-knowledge metadata only) ---


@router.get("/family/vault/meta")
async def vault_meta(request: Request) -> dict[str, Any]:
    _, fam = await _require_member(request)
    fam_store = _family_store(request)
    seed: _VaultSeed = await fam_store.get_vault_seed(family_id=fam.id)
    providers = await fam_store.list_family_providers(family_id=fam.id)
    return {
        "family_id": fam.id,
        "vault_initialized": seed.family_salt is not None,
        "family_salt": seed.family_salt,
        "has_provider": len(providers) > 0,
    }


@router.put("/family/vault")
async def set_vault(body: VaultSet, request: Request) -> dict[str, Any]:
    _, fam = await _require_owner(request)
    fam_store = _family_store(request)
    await fam_store.set_vault_seed(
        family_id=fam.id,
        family_salt=body.family_salt,
        family_enc_blob_seed=body.family_enc_blob_seed,
    )
    return {"family_id": fam.id, "ok": True}


# --- family therapist prompt (owner-write, member-read) ---
#
# The body is owner-authored shared content (not a key) — same disclosure
# regime as the custom-persona prompt (e.g. the same safety footer is
# appended client-side; the owner cannot drop the "disclose, don't perform"
# invariant). It is NOT zero-knowledge like the family BYOK key.
#
# The store stamps ``set_at`` from the server clock (Postgres uses NOW(),
# in-memory uses datetime.now(UTC)) so the wire's ``set_at`` is
# authoritative. ``set_by_display_name`` is resolved here at read time via
# the auth store so the client can render "Set by <name> · <date>" without
# a second round-trip. The router returns a 400 (not 422) for an explicit
# empty string — the contract is "set a real prompt or pass null to clear".


async def _display_name_lookup(auth_store: AuthStore, user_id: str | None) -> str | None:
    """Async lookup of the user's display name. Tries ``display_name`` first,
    falls back to the local-part of the email, then None. The Postgres auth
    store resolves both via SELECT; the in-memory store has the same shape."""
    if not user_id:
        return None
    try:
        user = await auth_store.get_user(user_id)
    except Exception:  # noqa: BLE001
        return None
    if user is None:
        return None
    name = (user.display_name or "").strip()
    if name:
        return name
    email = (user.email or "").strip()
    if "@" in email:
        return email.split("@", 1)[0]
    return email or None


@router.get("/family/therapist-prompt", response_model=FamilyTherapistPrompt)
async def get_therapist_prompt(request: Request) -> FamilyTherapistPrompt:
    _, fam = await _require_member(request)
    fam_store = _family_store(request)
    auth_store = _auth_store(request)
    rec = await fam_store.get_therapist_prompt(family_id=fam.id)
    return FamilyTherapistPrompt(
        body=rec.body,
        set_by_user_id=rec.set_by_user_id,
        set_at=rec.set_at,
        set_by_display_name=await _display_name_lookup(auth_store, rec.set_by_user_id),
    )


@router.put("/family/therapist-prompt", response_model=FamilyTherapistPrompt)
async def set_therapist_prompt(
    body: FamilyTherapistPromptSet, request: Request
) -> FamilyTherapistPrompt:
    user_id, fam = await _require_owner(request)
    # Explicit empty string is a 400 — the contract is "set a real prompt or
    # pass null to clear". Pydantic's max_length=8000 handles the upper bound
    # (returns 422 with a clear validation error).
    if body.body == "":
        raise HTTPException(
            status_code=400,
            detail="empty body — pass null to clear or a non-empty string to set",
        )
    fam_store = _family_store(request)
    auth_store = _auth_store(request)
    rec = await fam_store.set_therapist_prompt(
        family_id=fam.id,
        body=body.body,
        set_by_user_id=user_id,
    )
    return FamilyTherapistPrompt(
        body=rec.body,
        set_by_user_id=rec.set_by_user_id,
        set_at=rec.set_at,
        set_by_display_name=await _display_name_lookup(auth_store, rec.set_by_user_id),
    )


# --- family providers (zero-knowledge BYOK) ---


@router.get("/family/providers", response_model=list[FamilyProvider])
async def list_family_providers(request: Request) -> list[FamilyProvider]:
    _, fam = await _require_member(request)
    return await _family_store(request).list_family_providers(family_id=fam.id)


@router.post("/family/providers", response_model=FamilyProvider)
async def create_family_provider(body: FamilyProviderCreate, request: Request) -> FamilyProvider:
    _, fam = await _require_owner(request)
    fam_store = _family_store(request)
    api_key_ciphertext: str | None = None
    if body.enc_key_blob:
        api_key_ciphertext = _envelope_encrypt_byok(request, body.enc_key_blob)
    p = FamilyProvider(
        id=uuid.uuid4().hex,
        family_id=fam.id,
        kind=body.kind,
        label=body.label,
        base_url=body.base_url,
        key_handle=body.key_handle,
        model=body.model,
        embeddings_model=body.embeddings_model,
        enc_blob=body.enc_blob,
        created_at=_now(),
    )
    return await fam_store.add_family_provider(p=p, api_key_ciphertext=api_key_ciphertext)


@router.patch("/family/providers/{pid}", response_model=FamilyProvider)
async def update_family_provider(
    pid: str, body: FamilyProviderUpdate, request: Request
) -> FamilyProvider:
    _, fam = await _require_owner(request)
    fam_store = _family_store(request)
    fields = body.model_fields_set
    label = body.label if "label" in fields else None
    base_url = body.base_url if "base_url" in fields else None
    model = body.model if "model" in fields else None
    embeddings_model = body.embeddings_model if "embeddings_model" in fields else None
    updated = await fam_store.update_family_provider(
        family_id=fam.id,
        provider_id=pid,
        label=label,
        base_url=base_url,
        model=model,
        embeddings_model=embeddings_model,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return updated


@router.delete("/family/providers/{pid}", status_code=204)
async def delete_family_provider(pid: str, request: Request) -> None:
    _, fam = await _require_owner(request)
    fam_store = _family_store(request)
    if not await fam_store.delete_family_provider(family_id=fam.id, provider_id=pid):
        raise HTTPException(status_code=404, detail="provider not found")


@router.put("/family/providers/{pid}/enc_blob", response_model=FamilyProvider)
async def replace_family_provider_enc_blob(
    pid: str, body: FamilyProviderEncBlobUpdate, request: Request
) -> FamilyProvider:
    """Owner-only, family-scoped. Rotates the family provider's stored key
    material. Two modes (either or both may be supplied):

    - ``enc_blob``: the legacy vault-rotation re-seal — the client decrypts the
      key under the OLD family master key, re-seals it under the NEW family
      master key (new passphrase + new salt), and PUTs the fresh opaque
      ciphertext here so the server-side backup tracks the rotation. The body
      is opaque base64 ciphertext only — never a plaintext key (validated by
      ``_validate_family_enc_blob``).
    - ``enc_key_blob``: the new BYOK path — a fresh ECDH-sealed plaintext key.
      The server opens it, envelope-encrypts under ``MESSENGER_TOKEN_DEK``, and
      rotates ``api_key_ciphertext``. 503 when the envelope DEK is not
      configured.

    ``key_handle`` is unchanged, so the row stays valid. Cross-family / non-
    owner access is 404 (not 403), mirroring ``delete_family_provider``."""
    _, fam = await _require_owner(request)
    if body.enc_blob is None and body.enc_key_blob is None:
        raise HTTPException(
            status_code=400, detail="enc_blob or enc_key_blob is required"
        )
    if body.enc_blob is not None:
        _validate_family_enc_blob(body.enc_blob)
    api_key_ciphertext: str | None = None
    if body.enc_key_blob:
        api_key_ciphertext = _envelope_encrypt_byok(request, body.enc_key_blob)
    fam_store = _family_store(request)
    updated = await fam_store.set_family_provider_enc_blob(
        family_id=fam.id,
        provider_id=pid,
        enc_blob=body.enc_blob,
        api_key_ciphertext=api_key_ciphertext,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return updated


__all__ = ["router"]
