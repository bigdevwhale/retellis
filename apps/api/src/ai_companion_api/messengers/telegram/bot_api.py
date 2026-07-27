"""Thin wrapper over the Telegram Bot API HTTP endpoints.

Only the methods the adapter needs: ``getMe`` (validate), ``getUpdates``
(long poll), ``sendMessage``, ``sendChatAction``, ``answerCallbackQuery``.
The wrapper maps HTTP status codes to the adapter's exception taxonomy:

- 401 / 404 → ``TokenInvalid`` (permanent — token revoked or wrong).
- 5xx / network / timeout → ``TransientError`` (poller backs off and retries).
- 409 (``getUpdates`` conflict — another instance is polling) → ``FatalError``
  (the poller must stop so two servers don't fight over one bot).

``client`` is injectable so tests pass an ``httpx.MockTransport``; the real
path uses a per-call ``httpx.AsyncClient`` with a sane timeout. We never share
a client across calls (Telegram has no connection-affinity requirement and a
per-call client is simplest to reason about for long polls).
"""

from __future__ import annotations

from typing import Any

import httpx

from ..base import FatalError, TokenInvalid, TransientError

API_BASE = "https://api.telegram.org"


class TelegramBotAPI:
    def __init__(self, base_url: str = API_BASE, client: httpx.AsyncClient | None = None) -> None:
        self._base = base_url.rstrip("/")
        # When a client is injected (tests), the caller owns its lifecycle.
        self._client = client

    async def _post(self, token: str, method: str, body: dict[str, Any]) -> object:
        url = f"{self._base}/bot{token}/{method}"
        own = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            try:
                r = await client.post(url, json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise TransientError(f"telegram {method} network error: {exc}") from exc
            return self._handle(method, r)
        finally:
            if own:
                await client.aclose()

    @staticmethod
    def _handle(method: str, r: httpx.Response) -> object:
        # Don't log the body — it may echo the token in error descriptions on
        # 401. We surface only the mapped exception.
        if r.status_code in (401, 403, 404):
            raise TokenInvalid(f"telegram {method} rejected token ({r.status_code})")
        if r.status_code == 409:
            raise FatalError(f"telegram {method}: 409 conflict (another poller active)")
        if r.status_code >= 500:
            raise TransientError(f"telegram {method} server error ({r.status_code})")
        if r.status_code >= 400:
            # 4xx other than auth: treat as transient (rate limit 429, bad
            # request 400 that may be retried with a corrected payload). The
            # poller logs and backs off rather than crashing the row.
            raise TransientError(f"telegram {method} client error ({r.status_code})")
        try:
            data = r.json()
        except ValueError as exc:
            raise TransientError(f"telegram {method} returned non-JSON") from exc
        if not isinstance(data, dict) or not data.get("ok"):
            # ``ok: false`` carries a human description; keep it out of logs to
            # avoid leaking token-adjacent context. The poller just retries.
            raise TransientError(f"telegram {method} returned ok=false")
        return data.get("result")

    async def get_me(self, token: str) -> dict:
        result = await self._post(token, "getMe", {})
        return result if isinstance(result, dict) else {}

    async def get_updates(self, token: str, *, offset: int | None, timeout: int) -> list[dict]:
        body: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            body["offset"] = offset
        # Long poll: the httpx timeout must exceed the Bot API timeout so we
        # don't cancel a legitimate wait. Add a 10s margin.
        own = self._client is None
        url = f"{self._base}/bot{token}/getUpdates"
        client = self._client or httpx.AsyncClient(timeout=timeout + 10.0)
        try:
            try:
                r = await client.post(url, json=body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise TransientError(f"telegram getUpdates network error: {exc}") from exc
            result = self._handle("getUpdates", r)
        finally:
            if own:
                await client.aclose()
        return result if isinstance(result, list) else []

    async def send_message(self, token: str, chat_id: int, text: str) -> dict:
        result = await self._post(token, "sendMessage", {"chat_id": chat_id, "text": text})
        return result if isinstance(result, dict) else {}

    async def send_chat_action(self, token: str, chat_id: int, action: str = "typing") -> dict:
        result = await self._post(token, "sendChatAction", {"chat_id": chat_id, "action": action})
        return result if isinstance(result, dict) else {}

    async def answer_callback_query(self, token: str, callback_id: str, text: str | None) -> dict:
        body: dict[str, Any] = {"callback_query_id": callback_id}
        if text is not None:
            body["text"] = text
        result = await self._post(token, "answerCallbackQuery", body)
        return result if isinstance(result, dict) else {}

    async def send_message_with_inline(
        self, token: str, chat_id: int, text: str, reply_markup: dict
    ) -> dict:
        result = await self._post(
            token, "sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        )
        return result if isinstance(result, dict) else {}


__all__ = ["TelegramBotAPI"]