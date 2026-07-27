"""``/v1/providers`` — CRUD for provider *metadata* + server-side envelope key.

The server stores, per provider:

- ``key_handle`` — an opaque client-chosen pointer (kept for back-comat with
  the legacy client vault; the new flow uses it as the per-turn lookup key).
- ``enc_blob`` — a legacy zero-knowledge at-rest backup (kept as a dead column;
  new clients send ``null``). The server could never decrypt it.
- ``api_key_ciphertext`` (migration 0023) — the API key envelope-encrypted
  under ``MESSENGER_TOKEN_DEK`` (``crypto/envelope.py``). The plaintext is the
  full key JSON payload (``{provider_kind, api_key, base_url, extra}``) — the
  same payload the per-turn ECDH-sealed ``enc_key_blob`` carried. The server
  CAN decrypt this (it holds the DEK): this is envelope encryption against
  DB-dump exposure, NOT zero-knowledge (honest disclosure — see CLAUDE.md
  "Security invariants"). The decrypted key lives only in request scope and is
  zeroized after the turn.

Onboarding flow (one-time): the client ECDH-seals the key to the server session
pubkey (``GET /v1/health`` ecdh_pub) and POSTs it as ``enc_key_blob``. The
server opens it with the session private key, re-wraps the plaintext with the
envelope DEK, stores ``api_key_ciphertext``, and zeroizes the plaintext. Per
turn the server envelope-decrypts ``api_key_ciphertext`` (no client blob
needed). The ``enc_key_blob`` field is router-local pydantic — it is NOT on the
contracts ``Provider`` model, so the drift check is unaffected.

A legacy/mock client that sends no ``enc_key_blob`` leaves
``api_key_ciphertext`` null (env-fallback / mock chain). Hosted mode without
``MESSENGER_TOKEN_DEK`` 503s on the envelope-encrypt step (the same policy as
the Telegram bot-token endpoints — ``make_envelope`` returns None and the
endpoint refuses rather than storing a key it can't protect).

Persistence: the store is the same ``MemoryStore`` used for events/memories —
in-memory in tests / zero-config, Postgres when ``COMPANION_USE_DB=1``. The
providers table (migration 0001 + ``enc_blob`` in 0007 +
``api_key_ciphertext`` in 0023) is the durable shape.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from ai_companion_contracts import Provider, ProviderKind
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..crypto.envelope import EnvelopeCipher
from ..deps import get_current_user_id, get_session_ecdh, get_store
from ..memory.store import MemoryStore
from ..vault.decrypt import DecryptError, decrypt_key_blob
from ..vault.zeroize import zeroized

router = APIRouter()


class ProviderCreate(BaseModel):
    kind: ProviderKind
    label: str
    base_url: str | None = None
    key_handle: str | None = None
    model: str | None = None
    # Embedding model for semantic memory recall (None = semantic memory off).
    # Not a key — the embedding call reuses the per-turn BYOK key.
    embeddings_model: str | None = None
    # Legacy zero-knowledge at-rest backup (now a dead column; kept for back-
    # comat). New clients send ``null``.
    enc_blob: str | None = None
    # One-time ECDH-sealed plaintext key (same shape as the per-turn
    # ``LlmStreamRequest.enc_key_blob``). The server opens it with the session
    # private key and envelope-encrypts the plaintext under
    # ``MESSENGER_TOKEN_DEK`` → ``api_key_ciphertext``. Router-local pydantic —
    # NOT on the contracts ``Provider`` model (no drift impact).
    enc_key_blob: str | None = None


class ProviderUpdate(BaseModel):
    # All fields optional — only the supplied keys are written (PATCH semantics).
    # Pydantic distinguishes "field absent" from "explicit null" via
    # ``model_fields_set``; we honor that for ``base_url`` / ``model`` /
    # ``embeddings_model`` (all nullable on the row) so a client can clear them
    # by sending the key with a JSON null. ``label`` is required-non-null on the
    # row, so a missing field falls back to the existing value (we don't allow
    # clearing label). The API key is NOT mutable here — rotation = delete +
    # re-add (a PATCH must never touch ``api_key_ciphertext``).
    label: str | None = None
    base_url: str | None = None
    model: str | None = None
    embeddings_model: str | None = None


UserId = Annotated[str, Depends(get_current_user_id)]
Store = Annotated[MemoryStore, Depends(get_store)]


def _envelope(request: Request) -> EnvelopeCipher | None:
    return getattr(request.app.state, "envelope", None)


@router.post("/providers", response_model=Provider)
async def create_provider(
    body: ProviderCreate, user_id: UserId, store: Store, request: Request
) -> Provider:
    pid = uuid.uuid4().hex
    api_key_ciphertext: str | None = None
    if body.enc_key_blob:
        envelope = _envelope(request)
        if envelope is None:
            # Same honest-message policy as the Telegram bot-token endpoints:
            # refuse rather than store a key the server can't protect.
            raise HTTPException(
                status_code=503, detail="envelope key not configured (MESSENGER_TOKEN_DEK)"
            )
        ecdh = get_session_ecdh(request)
        try:
            dk = decrypt_key_blob(body.enc_key_blob, ecdh.private_key)
        except DecryptError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Re-serialize the full key JSON payload so provider extras (e.g.
        # Bedrock's AWS triplet) survive the envelope round-trip. The plaintext
        # bytes are zeroized after the envelope wrap.
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
            api_key_ciphertext = envelope.encrypt_b64(bytes(plaintext))
    p = Provider(
        id=pid,
        user_id=user_id,
        kind=body.kind,
        label=body.label,
        base_url=body.base_url,
        key_handle=body.key_handle,
        model=body.model,
        embeddings_model=body.embeddings_model,
        enc_blob=body.enc_blob,
    )
    return await store.add_provider(p, api_key_ciphertext=api_key_ciphertext)


@router.get("/providers", response_model=list[Provider])
async def list_providers(user_id: UserId, store: Store) -> list[Provider]:
    return await store.list_providers(user_id=user_id)


@router.delete("/providers/{pid}", status_code=204)
async def delete_provider(pid: str, user_id: UserId, store: Store) -> None:
    deleted = await store.delete_provider(user_id=user_id, provider_id=pid)
    if not deleted:
        raise HTTPException(status_code=404, detail="provider not found")


@router.patch("/providers/{pid}", response_model=Provider)
async def update_provider(
    pid: str,
    body: ProviderUpdate,
    user_id: UserId,
    store: Store,
) -> Provider:
    # Load the existing row first so we can tell "absent key" from "explicit
    # null" (Pydantic's ``model_fields_set``), and to enforce the
    # user-owns-the-row invariant. A 404 here is also what a caller in the
    # wrong tenant sees, since the lookup is scoped by ``user_id``.
    existing = await store.get_provider(user_id=user_id, provider_id=pid)
    if existing is None:
        raise HTTPException(status_code=404, detail="provider not found")
    fields_set = body.model_fields_set
    label = body.label if "label" in fields_set else existing.label
    base_url = body.base_url if "base_url" in fields_set else existing.base_url
    model = body.model if "model" in fields_set else existing.model
    embeddings_model = (
        body.embeddings_model if "embeddings_model" in fields_set else existing.embeddings_model
    )
    updated = await store.update_provider(
        user_id=user_id,
        provider_id=pid,
        label=label,
        base_url=base_url,
        model=model,
        embeddings_model=embeddings_model,
    )
    if updated is None:
        # Race: the row was deleted between get_provider and update_provider.
        # Treat as not-found rather than silently succeed.
        raise HTTPException(status_code=404, detail="provider not found")
    return updated
