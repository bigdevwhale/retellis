"""Long-poll loop — one asyncio task per active messenger bot.

The poller envelope-decrypts the bot token once per cycle (the source bytearray
is zeroized after the cycle), calls ``adapter.poll_once``, dispatches each
update through ``commands.handle_update``, persists the ``next_offset`` cursor,
and backs off on transient errors. A ``TokenInvalid`` or ``FatalError`` marks
the row ``error`` and stops the task (the web UI shows the operator/user a
re-bind prompt).

Backoff: 1s → 2s → 4s → … → 30s ceiling, reset to 1s on a clean cycle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import Settings
from ..crypto.envelope import EnvelopeCipher, EnvelopeDecryptError
from ..memory.store import MemoryStore
from ..vault.session_ecdh import SessionECDH
from .base import FatalError, MessengerAdapter, TokenInvalid, TransientError
from .store import MessengerRecord, MessengerStore
from .telegram.commands import BotSession, handle_update

logger = logging.getLogger(__name__)

_BACKOFF_FLOOR = 1.0
_BACKOFF_CEILING = 30.0
# Short pause when a cycle returned no updates (avoid hot-spinning the API).
_IDLE_PAUSE = 0.5


@dataclass
class PollerDeps:
    """Shared deps every poller shares (held on app.state)."""

    settings: Settings
    store: MemoryStore
    ecdh: SessionECDH
    envelope: EnvelopeCipher | None
    messenger_store: MessengerStore
    adapter: MessengerAdapter
    public_origin: str


class MessengerPoller:
    def __init__(self, deps: PollerDeps, messenger: MessengerRecord) -> None:
        self._deps = deps
        self.messenger = messenger
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._backoff = _BACKOFF_FLOOR
        self.conversation_id = BotSession.fresh_conversation_id()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name=f"poller:{self.messenger.id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5.0)
            except (TimeoutError, Exception):  # noqa: BLE001
                self._task.cancel()

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        m = self.messenger
        logger.info("telegram poller started (messenger=%s persona=%s)", m.id, m.persona_id)
        while not self._stop.is_set():
            try:
                token = await self._plaintext_token()
            except EnvelopeDecryptError as exc:
                # Can't decrypt the bot token — the envelope key rotated away
                # (self-hosted ephemeral key lost on restart). Mark error and stop.
                await self._mark_error(f"bot token undecryptable: {exc}")
                return
            try:
                updates = await self._deps.adapter.poll_once(
                    token, m.next_offset, timeout=self._deps.settings.messenger_poll_timeout
                )
                self._backoff = _BACKOFF_FLOOR
                for upd in updates:
                    session = BotSession(
                        messenger=m,
                        adapter=self._deps.adapter,
                        settings=self._deps.settings,
                        store=self._deps.store,
                        ecdh=self._deps.ecdh,
                        envelope=self._deps.envelope,
                        messenger_store=self._deps.messenger_store,
                        public_origin=self._deps.public_origin,
                        bot_token=token,
                        conversation_id=self.conversation_id,
                    )
                    await handle_update(session, upd)
                    m.next_offset = max(m.next_offset, upd.update_id + 1)
                    await self._save_offset()
                if not updates:
                    await asyncio.sleep(_IDLE_PAUSE)
            except TokenInvalid as exc:
                logger.warning("telegram poller token invalid (messenger=%s): %s", m.id, exc)
                await self._mark_error("bot token rejected by Telegram")
                return
            except FatalError as exc:
                logger.warning("telegram poller fatal (messenger=%s): %s", m.id, exc)
                await self._mark_error(f"fatal: {exc}")
                return
            except TransientError as exc:
                logger.warning("telegram poller transient (messenger=%s): %s", m.id, exc)
                await self._sleep_backoff()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never let the loop die silently
                logger.exception("telegram poller unexpected error (messenger=%s): %s", m.id, exc)
                await self._sleep_backoff()
        logger.info("telegram poller stopped (messenger=%s)", m.id)

    async def _plaintext_token(self) -> str:
        """Envelope-decrypt the bot token for one poll cycle.

        Returns a ``str`` (the adapter builds a URL from it). Honest-limit: once
        decoded to ``str`` the bytes are on the managed heap and can't be wiped
        by us — the source ciphertext is the only thing we control, and the
        envelope key never leaves the process. This is the same honest-zeroize
        disclosure as the BYOK path; we don't claim "erased from all memory".
        """
        env = self._deps.envelope
        if env is None:
            raise EnvelopeDecryptError("envelope cipher unavailable (feature disabled)")
        return env.decrypt_b64(self.messenger.bot_token_ciphertext).decode("utf-8")

    async def _save_offset(self) -> None:
        try:
            await self._deps.messenger_store.update(self.messenger.id, next_offset=self.messenger.next_offset)
        except Exception:  # noqa: BLE001
            logger.warning("telegram offset persist failed (messenger=%s)", self.messenger.id)

    async def _mark_error(self, message: str) -> None:
        try:
            await self._deps.messenger_store.update(
                self.messenger.id, status="error", last_error=message
            )
            self.messenger.status = "error"
            self.messenger.last_error = message
        except Exception:  # noqa: BLE001
            logger.exception("telegram mark_error failed (messenger=%s)", self.messenger.id)

    async def _sleep_backoff(self) -> None:
        await asyncio.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, _BACKOFF_CEILING)


__all__ = ["MessengerPoller", "PollerDeps"]