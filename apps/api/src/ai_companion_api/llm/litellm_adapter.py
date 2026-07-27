"""Thin wrapper over ``litellm.acompletion`` for real provider streaming.

litellm is imported lazily so the mock path (and the test suite) works without
it installed; the Docker image installs it. The API key is held only for the
duration of one call and dropped on return — the caller zeroizes the source
bytearray.

K1: the BYOK key is held as a ``bytearray`` (the same buffer the caller
zeroizes after the chain runs), NOT as a decoded ``str`` on ``self``. A
decoded ``str`` is immutable and would survive the caller's ``zeroized()``
context for the whole request lifetime, contradicting the
``vault/zeroize.py`` invariant. Each call decodes the bytearray into a
short-lived local ``str`` for the single LiteLLM invocation — the documented
honest limit (the managed-heap str is transient and GC'd; the source buffer
is wiped). Env-fallback keys are process-lifetime ``str`` and stored as-is
(per the security model, those are not zeroized).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .types import LlmUsage

# Rough USD per 1M tokens (prompt, completion). Unknown models → 0.0; the
# budget tracker (Phase 4) refines this. Used only for the usage SSE event.
# Keys are substrings, not exact ids — the lookup walks the map in order and
# returns the first hit. Substrings are kept unambiguous (e.g. ``claude-3-5``
# would also match ``claude-3-5-sonnet``; we list the most common names
# first and let it match whichever shows up first).
_PRICES_PER_M: list[tuple[str, tuple[float, float]]] = [
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.00)),
    ("gpt-4.1-mini", (0.40, 1.60)),
    ("gpt-4.1", (2.00, 8.00)),
    ("o3", (10.00, 40.00)),
    ("o4-mini", (1.10, 4.40)),
    ("claude-3-5-haiku", (0.80, 4.00)),
    ("claude-3-5-sonnet", (3.00, 15.00)),
    ("claude-haiku-4-5", (1.00, 5.00)),
    ("claude-sonnet-4-5", (3.00, 15.00)),
    ("claude-opus-4-5", (15.00, 75.00)),
    ("claude-haiku", (0.80, 4.00)),
    ("gemini-1.5-flash", (0.075, 0.30)),
    ("gemini-2.0-flash", (0.10, 0.40)),
    ("gemini-2.5-flash", (0.30, 1.20)),
    ("amazon.nova-lite", (0.06, 0.24)),
    ("amazon.nova-pro", (0.80, 3.20)),
    ("llama3.3", (0.0, 0.0)),
]


def _cost(model: str, pt: int, ct: int) -> float:
    for key, (ppm, cpm) in _PRICES_PER_M:
        if key in model:
            return (pt / 1_000_000) * ppm + (ct / 1_000_000) * cpm
    return 0.0


class LiteLLMAdapter:
    def __init__(
        self,
        provider_kind: str,
        api_key: bytearray | str,
        base_url: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.provider_kind = provider_kind
        # bytearray (BYOK, zeroized by caller) or str (env fallback, process-lifetime).
        self._api_key = api_key
        self._base_url = base_url
        # Per-kind extras: aws_* for Bedrock, api_version for Azure. None
        # everywhere else. Adapter picks the litellm kwargs from this map.
        self._extra = extra
        self._usage: LlmUsage | None = None
        # P2: cumulative usage of ``complete()`` calls (judge / extract /
        # consolidation / relationship note), keyed by model — the hidden
        # spend that used to go unmetered. Drained once per turn by the
        # router into real ``usage`` rows so the dashboard and the monthly
        # budget see the FULL cost of a turn, not just the chat stream.
        self._utility: dict[str, tuple[int, int, float]] = {}

    def _api_key_str(self) -> str:
        """Decode the BYOK bytearray for one call, or return the env str as-is.

        Returns a short-lived local — the caller must not retain it. Decoding
        per call (rather than caching on ``self``) is what keeps the immutable
        ``str`` from outliving the caller's zeroize window.
        """
        if isinstance(self._api_key, bytearray):
            return self._api_key.decode("utf-8")
        return self._api_key

    async def stream(self, messages: list[dict[str, str]], model: str) -> AsyncIterator[str]:
        import litellm  # lazy: mock path & tests don't require it

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # Bedrock takes the AWS credential triplet instead of ``api_key``. The
        # ``extra`` map is the same shape the BYOK picker writes (and the
        # env-fallback builder in provider.py). For all other kinds, ``extra``
        # is None or only carries ``api_version`` (Azure) and we keep the
        # default Bearer-key path.
        if self.provider_kind == "bedrock":
            if not self._extra:
                raise LlmCallError("bedrock: missing aws_secret_access_key/region")
            kwargs["aws_access_key_id"] = self._api_key_str()
            kwargs["aws_secret_access_key"] = self._extra["aws_secret_access_key"]
            kwargs["aws_region_name"] = self._extra["aws_region_name"]
        else:
            kwargs["api_key"] = self._api_key_str()
        if self._base_url:
            kwargs["api_base"] = self._base_url
        if self._extra and self._extra.get("api_version"):
            # Azure: pass the api_version alongside api_base.
            kwargs["api_version"] = self._extra["api_version"]

        pt = ct = 0
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                content = getattr(delta, "content", None) if delta else None
                if content:
                    pt_local, ct_local = _count_chunk(chunk)
                    ct += ct_local
                    yield content
                u = getattr(chunk, "usage", None)
                if u is not None:
                    pt = int(getattr(u, "prompt_tokens", 0) or 0)
                    ct = int(getattr(u, "completion_tokens", 0) or ct)
        except Exception as exc:
            # Surface a clean, redactable signal; the router redacts and emits
            # a fallback/error event. Do NOT include the key in the message.
            raise LlmCallError(f"provider call failed: {type(exc).__name__}") from exc

        if pt == 0 and ct == 0:
            # Fallback estimate if the provider didn't return usage.
            pt = sum(len(m.get("content", "").split()) for m in messages)
        self._usage = LlmUsage(
            provider_kind=self.provider_kind,
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_usd=_cost(model, pt, ct),
        )

    async def complete(self, messages: list[dict[str, str]], model: str) -> str:
        """Non-streaming completion — used by the salience judge. Does NOT touch
        ``self._usage`` (the stream's usage is preserved for the SSE ``usage``
        event). Reuses the same key + base_url as the turn so the judge runs on
        the same provider the user already paid for the turn on. Raises
        ``LlmCallError`` (no key material) on failure; the judge catches it and
        falls back to the heuristic. Each call's tokens accumulate into the
        per-model utility counter (P2) so the router can meter the post-turn
        work into honest usage rows."""
        import litellm  # lazy

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if self.provider_kind == "bedrock":
            if not self._extra:
                raise LlmCallError("bedrock: missing aws_secret_access_key/region")
            kwargs["aws_access_key_id"] = self._api_key_str()
            kwargs["aws_secret_access_key"] = self._extra["aws_secret_access_key"]
            kwargs["aws_region_name"] = self._extra["aws_region_name"]
        else:
            kwargs["api_key"] = self._api_key_str()
        if self._base_url:
            kwargs["api_base"] = self._base_url
        if self._extra and self._extra.get("api_version"):
            kwargs["api_version"] = self._extra["api_version"]
        try:
            response = await litellm.acompletion(**kwargs)
            self._track_utility(model, getattr(response, "usage", None))
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise LlmCallError(f"judge call failed: {type(exc).__name__}") from exc

    def _track_utility(self, model: str, usage: object | None) -> None:
        pt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        ct = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
        if pt == 0 and ct == 0:
            return
        prev_pt, prev_ct, prev_cost = self._utility.get(model, (0, 0, 0.0))
        self._utility[model] = (
            prev_pt + pt,
            prev_ct + ct,
            prev_cost + _cost(model, pt, ct),
        )

    def drain_utility_usage(self) -> list[LlmUsage]:
        """Return-and-reset the accumulated ``complete()`` usage, one entry per
        model. Called once per turn after the post-turn work so the router can
        persist honest usage rows for the judge/extract/consolidation calls."""
        out = [
            LlmUsage(self.provider_kind, model, pt, ct, cost)
            for model, (pt, ct, cost) in self._utility.items()
        ]
        self._utility = {}
        return out

    def last_usage(self) -> LlmUsage:
        if self._usage is None:
            return LlmUsage(self.provider_kind, "", 0, 0, 0.0)
        return self._usage


def _count_chunk(chunk: object) -> tuple[int, int]:
    u = getattr(chunk, "usage", None)
    if u is None:
        return (0, 0)
    return (
        int(getattr(u, "prompt_tokens", 0) or 0),
        int(getattr(u, "completion_tokens", 0) or 0),
    )


class LlmCallError(Exception):
    """Raised when a real provider call fails. Message carries no key material."""
