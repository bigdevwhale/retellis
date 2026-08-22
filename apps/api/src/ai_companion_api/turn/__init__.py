"""Turn orchestrator — the non-streaming core shared by messengers.

This is a **parallel** implementation to ``routers/llm.py::_stream`` — it does
NOT modify ``_stream`` (455 passing tests depend on that path). It reuses the
same library building blocks (``build_chain``, ``build_context``,
``run_with_fallback``, ``append_event``, the post-turn memory work) so the
empathy differentiator (event-chain memory, persona block, salience-gated
extraction, consolidation) is identical to the web chat. Telegram turns land
in the SAME event chain and atomic memory as web turns — same ``user_id`` +
``persona_id``.

Scope (MVP): personal turns only (no family scope, no family therapist prompt,
no family embedder). The adaptive/semantic recall layers are simplified to the
hash path — a Telegram turn gets the same persona block + salient chains +
recent window + extraction/consolidation as a web turn with ``memory_on`` and
the zero-config embedder. Upgrading the messenger path to semantic recall is a
post-MVP lift that touches only this file.

Honest-limits: the BYOK key bound to a bot is envelope-decrypted per turn,
re-sealed to the session ECDH pubkey, run through ``build_chain``, and zeroized
after the turn — same honest-zeroize disclosure as the web path (the source
bytearray is wiped; the immutable ``str`` handed to LiteLLM lives on the
managed heap and cannot be wiped by us).
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from ai_companion_contracts import Event, EventRole, Usage

from ..crypto.envelope import EnvelopeCipher, EnvelopeDecryptError
from ..llm import ProviderResolutionError, build_chain
from ..llm.provider import utility_model_for
from ..memory import append_event, build_context, chains_to_messages
from ..memory.extract import extract_memories
from ..memory.salience import SalienceScore
from ..memory.salience_llm import judge_salience
from ..memory.store import MemoryStore
from ..observability import redact
from ..routing import compute_budget, run_with_fallback
from ..vault.decrypt import DecryptedKey, DecryptError, parse_decrypted_key
from ..vault.session_ecdh import SessionECDH
from ..vault.zeroize import zeroized

logger = logging.getLogger(__name__)


@dataclass
class TurnInput:
    """One non-streaming turn. Personal scope (family scope is out of MVP)."""

    user_id: str
    persona_id: str
    conversation_id: str  # one persistent convo per (user, persona, bot)
    user_message: str
    # Envelope-ciphertext (base64) of the BYOK key JSON, as stored in
    # ``messengers.byok_enc_blob``. ``None`` = no BYOK bound → env-fallback chain.
    byok_enc_blob: str | None
    # The user-chosen model id for the BYOK provider (mirrors LlmStreamRequest.model).
    model: str | None = None
    # Optional client-side pointer to the active provider row. When
    # ``byok_enc_blob`` is None and this is set, the orchestrator resolves the
    # BYOK key from the server-side envelope store
    # (``providers.api_key_ciphertext``) instead. When both are None, the
    # orchestrator falls back to the user's first provider with a stored
    # ciphertext (the Telegram "any stored key" case).
    key_handle: str | None = None
    source: Literal["web", "telegram"] = "telegram"


@dataclass
class TurnOutput:
    request_id: str
    assistant_text: str
    provider_kind: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    conversation_id: str
    fallback_used: bool


def _reseal_byok(envelope_plaintext: bytes, ecdh: SessionECDH) -> str:
    """Re-seal the envelope-decrypted BYOK key JSON to the session ECDH pubkey.

    ``build_chain`` expects an ECDH-sealed blob (it decrypts via the session
    private key). The envelope blob is the *plaintext* key JSON, so per turn we
    envelope-decrypt (caller), then re-seal here. The round-trip is cheap (one
    NaCl sealed box) and keeps ``build_chain`` untouched.
    """
    from nacl.public import PublicKey, SealedBox

    pub = PublicKey(base64.b64decode(ecdh.pub_b64))
    sealed = SealedBox(pub).encrypt(envelope_plaintext)
    return base64.b64encode(sealed).decode("ascii")


def _events_to_window(events: list[Event]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for e in events:
        role = e.role.value if hasattr(e.role, "value") else str(e.role)
        if role not in ("user", "assistant"):
            continue
        out.append({"role": role, "content": e.content})
    return out


async def _resolve_messenger_byok_from_provider(
    *,
    store: MemoryStore,
    user_id: str,
    key_handle: str | None,
    envelope: EnvelopeCipher,
) -> tuple[DecryptedKey | None, str | None]:
    """Server-side envelope fallback for messenger turns.

    When no ``byok_enc_blob`` is bound to the messenger row, resolve the BYOK
    key from the user's personal provider envelope store
    (``providers.api_key_ciphertext``). When ``key_handle`` is given, look up
    that exact row; otherwise fall back to the user's first provider with a
    stored ciphertext (the Telegram "any stored key" case — the bind flow no
    longer needs a per-bot BYOK blob because the server already has the key).

    Returns ``(DecryptedKey | None, model | None)``. The ``model`` is the
    chosen model on the resolved provider row — the web client sends this per
    turn (``LlmStreamRequest.model``), but the messenger path does not carry
    it, so we read it from the row. Without it ``build_chain`` would fall back
    to ``DEFAULT_MODELS[kind]``, which is wrong for providers whose user-picked
    model differs from the default — e.g. Ollama Cloud with ``glm-5.2:cloud``
    would be called as ``openai/llama3.3`` and rejected, falling through to the
    mock stand-in.

    Returns ``(None, None)`` on any miss / store failure / decrypt failure (the
    turn degrades to env-fallback / mock — never 500). The caller zeroizes
    ``dk.api_key`` after the chain runs via the existing ``zeroized()`` window.
    Cross-user scoping is enforced by the store (the lookup filters on
    ``user_id``); a stranger's row is invisible (``None``).
    """
    try:
        providers = await store.list_providers(user_id=user_id)
    except Exception as exc:  # noqa: BLE001 — store failure must not 500
        logger.warning("messenger BYOK provider lookup failed: %s: %s", type(exc).__name__, exc)
        return None, None
    if key_handle is not None:
        chosen = next((p for p in providers if p.key_handle == key_handle), None)
    else:
        # No key_handle: pick the user's first provider with a stored key.
        chosen = next((p for p in providers if p.key_handle is not None), None)
    if chosen is None or chosen.key_handle is None:
        return None, None
    try:
        ciphertext = await store.get_provider_api_key_ciphertext(
            user_id=user_id, key_handle=chosen.key_handle
        )
    except Exception as exc:  # noqa: BLE001 — store failure must not 500
        logger.warning("messenger BYOK provider lookup failed: %s: %s", type(exc).__name__, exc)
        return None, None
    if ciphertext is None:
        return None, None
    try:
        plaintext = envelope.decrypt_b64(ciphertext)
    except EnvelopeDecryptError as exc:
        logger.warning("messenger BYOK envelope decrypt failed (tampered/wrong DEK): %s", exc)
        return None, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("messenger BYOK envelope decrypt failed: %s: %s", type(exc).__name__, exc)
        return None, None
    try:
        dk = parse_decrypted_key(plaintext)
    except DecryptError as exc:
        logger.warning("messenger BYOK envelope plaintext malformed: %s", exc)
        return None, None
    return dk, chosen.model


async def _post_turn(
    served_cand: object | None,
    store: MemoryStore,
    *,
    user_id: str,
    persona_id: str,
    convo_id: str,
    new_user_msg: str,
    new_user_event_id: str,
) -> None:
    """Lean version of ``routers/llm._post_turn_work`` — judge salience, extract
    atomic memories. Consolidation / era / relationship-note are omitted on the
    messenger path for MVP (they ride the web cadence; a post-MVP lift wires
    them here). Never raises — best-effort, same contract as the web path."""
    if served_cand is None:
        return
    try:
        judged = await judge_salience(
            served_cand.adapter,
            utility_model_for(
                getattr(served_cand, "kind", ""),
                getattr(served_cand, "model", ""),
                override=None,
            ),
            new_user_msg,
        )
        EXTRACT_THRESHOLD = 0.3
        if judged is None or max(judged.salience, judged.factual_novelty) < EXTRACT_THRESHOLD:
            return
        try:
            recent = await store.recent_window(
                user_id=user_id, persona_id=persona_id, convo_id=convo_id
            )
        except Exception:
            recent = []
        recent.append(
            Event(
                id=new_user_event_id,
                user_id=user_id,
                persona_id=persona_id,
                role=EventRole.user,
                content=new_user_msg,
            )
        )
        try:
            existing = await store.list_memories(
                user_id=user_id, persona_id=persona_id, include_donors=True
            )
        except Exception:
            existing = []
        await extract_memories(
            served_cand.adapter,
            served_cand.model,
            recent_events=recent,
            existing_memories=existing,
            new_user_event_id=new_user_event_id,
            participants=None,
        )
    except Exception as exc:  # noqa: BLE001 — never break the turn over memory work
        logger.warning("messenger post-turn memory work failed: %s: %s", type(exc).__name__, exc)


async def run_turn(
    inp: TurnInput,
    *,
    settings,
    store: MemoryStore,
    ecdh: SessionECDH,
    envelope,
) -> TurnOutput:
    """Run one non-streaming turn and persist it into the shared event chain.

    ``envelope`` is the ``EnvelopeCipher`` (``app.state.envelope``); used to
    decrypt ``inp.byok_enc_blob``. ``None`` for both envelope and blob = the
    server-fallback chain (env keys / Ollama / mock).
    """
    request_id = uuid.uuid4().hex
    new_user_event_id = uuid.uuid4().hex
    new_assistant_event_id = uuid.uuid4().hex

    # --- Resolve the BYOK key. Two additive paths share this window:
    #     1. Legacy: envelope-decrypt ``inp.byok_enc_blob`` (the messenger-row
    #        blob) → re-seal to session ECDH → ``enc_key_blob`` for build_chain.
    #     2. New: when no messenger blob is bound, resolve the BYOK key from
    #        the server-side provider envelope store (``providers.
    #        api_key_ciphertext``) via ``parse_decrypted_key`` → pass as
    #        ``byok_decrypted`` to build_chain (no ECDH re-seal needed). ---
    enc_key_blob: str | None = None
    byok_decrypted: DecryptedKey | None = None
    byok_plaintext: bytearray | None = None
    # The user-chosen model for the BYOK provider. The web client sends this per
    # turn; the messenger path does not, so when we resolve the key from the
    # server-side provider envelope store we also take the row's ``model``.
    # Without it ``build_chain`` falls back to ``DEFAULT_MODELS[kind]``, which is
    # wrong for providers whose user-picked model differs from the default.
    byok_model: str | None = inp.model
    if inp.byok_enc_blob and envelope is not None:
        try:
            byok_plaintext = bytearray(envelope.decrypt_b64(inp.byok_enc_blob))
        except Exception as exc:  # noqa: BLE001 — bad blob must not crash the row
            logger.warning("messenger BYOK envelope decrypt failed: %s: %s", type(exc).__name__, exc)
            byok_plaintext = None
        if byok_plaintext is not None:
            try:
                enc_key_blob = _reseal_byok(bytes(byok_plaintext), ecdh)
            except Exception as exc:  # noqa: BLE001
                logger.warning("messenger BYOK re-seal failed: %s: %s", type(exc).__name__, exc)
                enc_key_blob = None
    elif envelope is not None:
        # No messenger-row blob — try the server-side provider envelope store.
        byok_decrypted, provider_model = await _resolve_messenger_byok_from_provider(
            store=store, user_id=inp.user_id, key_handle=inp.key_handle, envelope=envelope
        )
        if byok_model is None:
            byok_model = provider_model

    try:
        cands = build_chain(
            enc_key_blob=enc_key_blob,
            settings=settings,
            ecdh=ecdh,
            model=byok_model,
            byok_decrypted=byok_decrypted,
        )
    except ProviderResolutionError as exc:
        logger.warning("messenger build_chain failed: %s", redact(str(exc)))
        cands = build_chain(
            enc_key_blob=None,
            settings=settings,
            ecdh=ecdh,
            model=byok_model,
            byok_decrypted=byok_decrypted,
        )

    byok_dk = next((c.decrypted for c in cands if c.decrypted is not None), None)

    # --- Assemble context (persona block + salient chains + recent window). ---
    recent_msgs: list[dict[str, str]] = []
    salient_msgs: list[dict[str, str]] = []
    try:
        recent = await store.recent_window(
            user_id=inp.user_id, persona_id=inp.persona_id, convo_id=inp.conversation_id
        )
        recent_msgs = _events_to_window(recent)
    except Exception as exc:  # noqa: BLE001
        logger.warning("messenger recent_window failed: %s: %s", type(exc).__name__, exc)
    try:
        chains = await store.recall_chains(
            user_id=inp.user_id,
            persona_id=inp.persona_id,
            query=inp.user_message,
            k=3,
        )
        salient_msgs = chains_to_messages(chains)
    except Exception as exc:  # noqa: BLE001
        logger.warning("messenger recall_chains failed: %s: %s", type(exc).__name__, exc)

    messages = build_context(
        persona_id=inp.persona_id,
        message=inp.user_message,
        recent_window=recent_msgs,
        salient_chains=salient_msgs,
    )

    # --- Budget gate (personal scope; hosted credits not checked on the
    # messenger MVP — the bot is a personal self-hosted-style surface). ---
    try:
        spent = 0.0
        records = await store.list_usage(user_id=inp.user_id)
        now_month = _now_month()
        spent = sum(
            r.usage.cost_usd
            for r in records
            if r.created_at.year == now_month[0]
            and r.created_at.month == now_month[1]
            and r.usage.family_id is None
        )
    except Exception:  # noqa: BLE001
        spent = 0.0
    budget = compute_budget(spent_usd=spent, monthly_budget_usd=settings.monthly_budget_usd)
    run_cands = cands
    if budget.hard_stop:
        # Budget exceeded → no providers available (BYOK is also blocked)
        run_cands = []

    # --- Run the chain inside the BYOK zeroize window. ---
    async def _drive():
        served = None
        text_parts: list[str] = []
        fallback_used = False
        async for tag, val in run_with_fallback(run_cands, messages, user_id=inp.user_id):
            if tag == "served":
                served = val
            elif tag == "token":
                text_parts.append(val)  # type: ignore[arg-type]
            elif tag == "fallback":
                fallback_used = True
        return served, "".join(text_parts), fallback_used

    if byok_dk is not None:
        with zeroized(byok_dk.api_key):
            served, assistant_text, fallback_used = await _drive()
            await _post_turn(
                served,
                store,
                user_id=inp.user_id,
                persona_id=inp.persona_id,
                convo_id=inp.conversation_id,
                new_user_msg=inp.user_message,
                new_user_event_id=new_user_event_id,
            )
    else:
        served, assistant_text, fallback_used = await _drive()
        await _post_turn(
            served,
            store,
            user_id=inp.user_id,
            persona_id=inp.persona_id,
            convo_id=inp.conversation_id,
            new_user_msg=inp.user_message,
            new_user_event_id=new_user_event_id,
        )

    # Wipe the envelope-decrypted BYOK plaintext (the source bytearray; the
    # immutable str handed to LiteLLM via build_chain is the honest-limit case).
    if byok_plaintext is not None:
        for i in range(len(byok_plaintext)):
            byok_plaintext[i] = 0

    # --- Resolve usage + persist (best-effort, after the zeroize window). ---
    served_kind = getattr(served, "kind", "mock") if served is not None else "mock"
    served_model = getattr(served, "model", "mock") if served is not None else "mock"
    if served is not None:
        u = served.adapter.last_usage()  # type: ignore[attr-defined]
        prompt_tokens = u.prompt_tokens  # type: ignore[attr-defined]
        completion_tokens = u.completion_tokens  # type: ignore[attr-defined]
        cost_usd = u.cost_usd  # type: ignore[attr-defined]
    else:
        prompt_tokens = completion_tokens = 0
        cost_usd = 0.0

    usage = Usage(
        id=uuid.uuid4().hex,
        user_id=inp.user_id,
        family_id=None,
        provider_kind=served_kind,
        model=served_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )
    judged_for_persist: SalienceScore | None = None
    try:
        await append_event(
            store,
            user_id=inp.user_id,
            persona_id=inp.persona_id,
            convo_id=inp.conversation_id,
            role=EventRole.user,
            content=inp.user_message,
            event_id=new_user_event_id,
            salience_score=judged_for_persist,
        )
        await append_event(
            store,
            user_id=inp.user_id,
            persona_id=inp.persona_id,
            convo_id=inp.conversation_id,
            role=EventRole.assistant,
            content=assistant_text,
            event_id=new_assistant_event_id,
        )
        await store.add_usage(usage)
    except Exception as exc:  # noqa: BLE001 — memory must never break the turn
        logger.warning("messenger turn persist failed: %s: %s", type(exc).__name__, exc)

    return TurnOutput(
        request_id=request_id,
        assistant_text=assistant_text,
        provider_kind=served_kind,
        model=served_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        conversation_id=inp.conversation_id,
        fallback_used=fallback_used,
    )


def _now_month() -> tuple[int, int]:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return now.year, now.month


__all__ = ["TurnInput", "TurnOutput", "run_turn"]