"""``/v1/messengers`` — per-user external-messenger bots (Telegram first).

All endpoints are authed (a Principal is required — same as ``/v1/providers``).
The bot token is stored ONLY as an envelope ciphertext; the wire never carries
it back (``bot_token_masked`` — last 4 chars — is the only token-derived string
surfaced). BYOK keys bound at handshake are envelope-wrapped after the server
decrypts the ECDH-sealed blob ONCE; the server can decrypt them at turn time
(honest-limits: this is envelope encryption, NOT zero-knowledge — the web page
says so).

Init → ``/start <token>`` in Telegram → web ``/connect/telegram`` → bind flow
lives across three calls:

  POST /v1/messengers/telegram         (init: validate getMe, pending_handshake)
  POST /v1/messengers/telegram/{id}/bind (handshake: envelope-wrap BYOK, active)
  PATCH/DELETE /v1/messengers/{id}      (persona, pause/resume, disconnect)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from ai_companion_contracts import (
    Messenger,
    MessengerKind,
    MessengerPatchRequest,
    MessengerStatus,
    TelegramBindRequest,
    TelegramInitRequest,
    TelegramInitResponse,
)
from fastapi import APIRouter, Depends, HTTPException, Request

from ..crypto.envelope import EnvelopeCipher
from ..deps import get_current_principal
from ..messengers.base import TokenInvalid
from ..messengers.connect_token import issue_connect_token, verify_connect_token
from ..messengers.polling import MessengerPoller, PollerDeps
from ..messengers.store import MessengerRecord, MessengerStore
from ..observability import redact
from ..vault.decrypt import DecryptError, decrypt_key_blob

logger = logging.getLogger(__name__)

router = APIRouter()

Principal = Annotated[object, Depends(get_current_principal)]  # ai_companion_contracts.Principal


def _masked_token(token: str) -> str:
    """Last 4 chars, the way the web shows a stored key — enough to recognize,
    never enough to use."""
    return ("…" + token[-4:]) if len(token) >= 4 else "…"


def _to_messenger_model(rec: MessengerRecord) -> Messenger:
    return Messenger(
        id=rec.id,
        user_id=rec.user_id,
        kind=MessengerKind(rec.kind),
        status=MessengerStatus(rec.status),
        persona_id=rec.persona_id,
        chat_id=rec.chat_id,
        bot_username=rec.bot_username,
        bot_token_masked=rec.bot_token_masked,
        byok_bound=rec.byok_enc_blob is not None,
        last_error=rec.last_error,
        last_seen_at=rec.last_seen_at,
        created_at=rec.created_at or datetime.now(UTC),
        updated_at=rec.updated_at or datetime.now(UTC),
    )


def _deps(request: Request) -> PollerDeps:
    deps = getattr(request.app.state, "messenger_deps", None)
    if deps is None:
        raise HTTPException(status_code=503, detail="messenger integration not initialized")
    return deps


def _store(request: Request) -> MessengerStore:
    store = getattr(request.app.state, "messenger_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="messenger integration not initialized")
    return store


def _envelope(request: Request) -> EnvelopeCipher | None:
    return getattr(request.app.state, "envelope", None)


def _pollers(request: Request) -> dict[str, MessengerPoller]:
    return getattr(request.app.state, "messenger_pollers", {})


async def _start_poller(request: Request, rec: MessengerRecord) -> None:
    """Spawn (or replace) the poller for ``rec``. No-op if long-poll is off."""
    deps = _deps(request)
    if not deps.settings.messenger_long_poll_enabled:
        return
    pollers = _pollers(request)
    existing = pollers.get(rec.id)
    if existing is not None:
        await existing.stop()
    poller = MessengerPoller(deps, rec)
    await poller.start()
    pollers[rec.id] = poller


async def _stop_poller(request: Request, messenger_id: str) -> None:
    pollers = _pollers(request)
    existing = pollers.pop(messenger_id, None)
    if existing is not None:
        await existing.stop()


@router.get("/messengers", response_model=list[Messenger])
async def list_messengers(principal: Principal, request: Request) -> list[Messenger]:
    store = _store(request)
    recs = await store.list_by_user(principal.user_id)  # type: ignore[attr-defined]
    return [_to_messenger_model(r) for r in recs]


@router.post("/messengers/telegram", response_model=TelegramInitResponse)
async def init_telegram(
    body: TelegramInitRequest, principal: Principal, request: Request
) -> TelegramInitResponse:
    """Validate the bot token (getMe), create a ``pending_handshake`` row, and
    return a short-lived connect token + deep-link URL.

    The bot token is envelope-encrypted before it touches the store; the row
    is keyed by (user_id, kind) so a re-init for the same user/bot is idempotent
    (returns the existing pending row, re-minting the connect token)."""
    deps = _deps(request)
    store = _store(request)
    envelope = _envelope(request)
    if envelope is None:
        raise HTTPException(status_code=503, detail="messenger envelope key not configured")

    # Validate the token against Telegram BEFORE storing anything. A bad
    # token must not create a row the poller would then fail on.
    try:
        info = await deps.adapter.validate_token(body.bot_token)
    except TokenInvalid as exc:
        raise HTTPException(status_code=400, detail=redact(f"bot token rejected: {exc}")) from exc
    except Exception as exc:  # noqa: BLE001 — transient network → 503, not a 500 stack dump
        logger.warning("telegram init validate failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail="could not reach Telegram to validate the token") from exc

    cipher = envelope.encrypt_b64(body.bot_token.encode("utf-8"))
    # Idempotent: if a row for (user, telegram) already exists, refresh its
    # token + persona and reuse the id (a re-init shouldn't fork a second bot).
    existing = next(
        (r for r in await store.list_by_user(principal.user_id) if r.kind == "telegram"),  # type: ignore[attr-defined]
        None,
    )
    if existing is not None:
        await store.update(
            existing.id,
            bot_token_ciphertext=cipher,
            bot_token_masked=_masked_token(body.bot_token),
            bot_username=info.username,
            persona_id=body.persona_id,
            status="pending_handshake",
            last_error=None,
        )
        rec = await store.get(existing.id)
        assert rec is not None
    else:
        rec = await store.create(
            user_id=principal.user_id,  # type: ignore[attr-defined]
            kind="telegram",
            bot_token_ciphertext=cipher,
            bot_token_masked=_masked_token(body.bot_token),
            persona_id=body.persona_id,
        )
        await store.update(rec.id, bot_username=info.username)

    token = issue_connect_token(messenger_id=rec.id, settings=deps.settings)
    url = f"{deps.public_origin.rstrip('/')}/connect/telegram?messenger={rec.id}&token={token}"
    expires_at = datetime.now(UTC) + timedelta(seconds=deps.settings.messenger_connect_token_ttl_seconds)
    return TelegramInitResponse(
        messenger=_to_messenger_model(rec),
        connect_token=token,
        connect_url=url,
        expires_at=expires_at,
    )


@router.post("/messengers/telegram/{messenger_id}/bind", response_model=Messenger)
async def bind_telegram(
    messenger_id: str,
    body: TelegramBindRequest,
    principal: Principal,
    request: Request,
) -> Messenger:
    """Complete the handshake: verify the connect token, envelope-wrap the
    BYOK key (if any), flip to ``active``, and start the poller.

    ``body.byok_enc_key_blob`` is the ECDH-sealed BYOK blob (same shape as
    ``LlmStreamRequest.byok_enc_key_blob``). The server decrypts it ONCE via
    the session private key and immediately re-wraps the plaintext key JSON with
    the envelope key — so ``byok_enc_blob`` is envelope ciphertext, not the
    ECDH blob (which would break on a session-keypair rotation). ``None`` =
    no BYOK; the bot uses the server-fallback chain."""
    deps = _deps(request)
    store = _store(request)
    envelope = _envelope(request)
    if envelope is None:
        raise HTTPException(status_code=503, detail="messenger envelope key not configured")

    rec = await store.get_for_user(messenger_id, principal.user_id)  # type: ignore[attr-defined]
    if rec is None:
        raise HTTPException(status_code=404, detail="messenger not found")

    # The bind request must carry the connect token proving the user went
    # through the handshake. We accept it as a query param (the web page has
    # it from the deep link) — re-issued here as the bind's own proof.
    connect_token = request.query_params.get("token")
    if not connect_token or not verify_connect_token(
        connect_token, messenger_id=rec.id, settings=deps.settings
    ):
        raise HTTPException(status_code=400, detail="invalid or expired connect token")

    byok_enc_blob: str | None = None
    if body.byok_enc_key_blob:
        try:
            dk = decrypt_key_blob(body.byok_enc_key_blob, deps.ecdh.private_key)
        except DecryptError as exc:
            raise HTTPException(status_code=400, detail=redact(f"BYOK blob rejected: {exc}")) from exc
        # Reconstruct the plaintext key JSON the orchestrator will re-seal per
        # turn. ``api_key_str()`` decodes the bytearray once; the source
        # bytearray is wiped below.
        payload = json.dumps(
            {
                "provider_kind": dk.provider_kind,
                "api_key": dk.api_key_str(),
                "base_url": dk.base_url,
                "extra": dk.extra,
            }
        ).encode("utf-8")
        byok_enc_blob = envelope.encrypt_b64(payload)
        # Honest-zeroize the source bytearray (the immutable str handed to
        # json.dumps is the managed-heap honest-limit case).
        for i in range(len(dk.api_key)):
            dk.api_key[i] = 0

    rec = await store.update(
        rec.id,
        byok_enc_blob=byok_enc_blob,
        status="active",
        last_error=None,
    )
    assert rec is not None
    await _start_poller(request, rec)
    return _to_messenger_model(rec)


@router.patch("/messengers/{messenger_id}", response_model=Messenger)
async def patch_messenger(
    messenger_id: str,
    body: MessengerPatchRequest,
    principal: Principal,
    request: Request,
) -> Messenger:
    store = _store(request)
    rec = await store.get_for_user(messenger_id, principal.user_id)  # type: ignore[attr-defined]
    if rec is None:
        raise HTTPException(status_code=404, detail="messenger not found")

    fields: dict[str, object] = {}
    if body.persona_id is not None:
        fields["persona_id"] = body.persona_id
    if body.status is not None:
        fields["status"] = body.status.value
    if fields:
        rec = await store.update(rec.id, **fields)
        assert rec is not None

    # Pause/resume toggles the poller lifecycle.
    if body.status == MessengerStatus.paused:
        await _stop_poller(request, rec.id)
    elif body.status == MessengerStatus.active:
        await _start_poller(request, rec)
    return _to_messenger_model(rec)


@router.delete("/messengers/{messenger_id}", status_code=204)
async def delete_messenger(messenger_id: str, principal: Principal, request: Request) -> None:
    store = _store(request)
    rec = await store.get_for_user(messenger_id, principal.user_id)  # type: ignore[attr-defined]
    if rec is None:
        # Idempotent (204 on missing target — same contract as the memory deletes).
        return
    await _stop_poller(request, rec.id)
    await store.delete(rec.id)


@router.get("/messengers/{messenger_id}/status")
async def messenger_status(
    messenger_id: str, principal: Principal, request: Request
) -> dict:
    store = _store(request)
    rec = await store.get_for_user(messenger_id, principal.user_id)  # type: ignore[attr-defined]
    if rec is None:
        raise HTTPException(status_code=404, detail="messenger not found")
    return {
        "status": rec.status,
        "persona_id": rec.persona_id,
        "chat_id": rec.chat_id,
        "last_error": rec.last_error,
        "last_seen_at": rec.last_seen_at,
        "byok_bound": rec.byok_enc_blob is not None,
    }


__all__ = ["router"]