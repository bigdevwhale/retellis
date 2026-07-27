"""Redaction of API-key material from logs and trace payloads.

Provider keys start with a known prefix (``sk-``, ``sk-ant-``, ``sk-or-``,
``sk-proj-`` …) OR a distinctive header scheme. We scrub these from any string
before it reaches a log line, an exception message, or a Langfuse span
attribute. The companion product must never emit a user's key; the
``test_redaction`` suite enforces this by grepping captured log output for the
secret portions.

Honest limit (I19): we redact by *known shape*, not by entropy. A generic
high-entropy backstop (any long alphanumeric run) would false-positive on the
UUIDs that litter our logs (event ids, user ids, session tokens are all 32-char
hex) and on base64 ``enc_blob`` ciphertext — destroying log readability without
reliably catching keys. So we cover the shapes we know:

- ``sk-…`` (OpenAI / Anthropic / OpenRouter / OpenAI project keys)
- ``AIza…`` (Google API keys — ``AIza`` + 35 base64-ish chars)
- ``Bearer <token>`` (Ollama Cloud + any bearer-auth key rides an
  ``Authorization: Bearer …`` header; the ``Bearer`` discriminator lets us
  redact the token with near-zero false-positive risk)
- ``paddle_…`` / ``pdl_…`` / ``yukassa_…`` / ``prodamus_…`` (billing-provider API
  keys / webhook secrets). A 20-char minimum after the prefix avoids clobbering
  the config field names that share it (``paddle_environment``,
  ``yukassa_shop_id``, ``prodamus_payform_url`` …); real provider secrets are
  far longer.
- card PANs — a 13–19 digit run (spaces/dashes tolerated) that passes the
  Luhn checksum. The Luhn check is the false-positive guard: plain long
  numbers in our logs (timestamps, cents, ids) don't validate, so they stay.

An opaque key with NONE of these shapes that nonetheless leaks into a log is
not caught here — it relies on the never-logged/zeroized path instead.
"""

from __future__ import annotations

import logging
import re

# Match a known key prefix / scheme followed by the secret token chars. We keep
# the prefix visible (so operators can see *that* a key leaked and which kind)
# but erase the secret portion. Group 1 is the kept prefix; group 2 the secret.
_KEY_RE = re.compile(r"(sk-(?:ant-|or-|proj-)?|AIza|Bearer\s+)([A-Za-z0-9_\-._~+]{4,})")

# Billing-provider tokens (Paddle / ЮKassa). Longer minimum (20+) so the
# config field names that share the prefix (``paddle_environment`` &
# ``yukassa_shop_id``) are NOT redacted — only real secrets are.
_BILLING_KEY_RE = re.compile(r"(paddle_|pdl_|yukassa_|prodamus_)([A-Za-z0-9_\-]{20,})")

# Telegram bot token — ``<bot_id>:<secret>`` where bot_id is 8-12 digits and the
# secret is 30+ base64-url chars (A-Za-z0-9_-). The colon discriminator makes this
# near-zero false-positive: nothing else in our logs is ``digits:long-token``.
# We keep the bot_id visible (it's in every Telegram API URL and not secret) and
# erase the secret half. ``enc_blob`` / ``bot_token_ciphertext`` are base64 with
# no colon, so they don't match here — they stay intact (ciphertext isn't a key).
_TG_BOT_TOKEN_RE = re.compile(r"(\d{8,12}:)([A-Za-z0-9_-]{30,})")

# Card PAN — a contiguous 13–19 digit run. Separators (spaces/dashes) are NOT
# tolerated: a dashed/spaced run is the shape of a UUID (``00000000-0000-…``),
# and an all-zero UUID is Luhn-valid, so separator-tolerance would clobber the
# UUIDs that litter our logs. A leaked PAN in a log is almost always a
# contiguous copy-paste; Luhn is validated per candidate so non-card digit runs
# (timestamps, kopecks, ids) still survive.
_PAN_RE = re.compile(r"\b(\d{13,19})\b")

_REDACTED = r"\1••••REDACTED••••"
_PAN_REDACTED = "••••REDACTED••••"


def _luhn_valid(digits: str) -> bool:
    """True iff ``digits`` (13–19 chars, digits only) passes the Luhn checksum."""
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_pan(text: str) -> str:
    """Redact Luhn-valid card PANs. A candidate that fails Luhn is left intact
    (it's not a card number — e.g. a timestamp). Nothing of a real PAN is kept:
    cardholder data is erased wholesale, not prefix-masked."""

    def repl(m: re.Match[str]) -> str:
        raw = m.group(1)
        digits = re.sub(r"\D", "", raw)
        return _PAN_REDACTED if _luhn_valid(digits) else raw

    return _PAN_RE.sub(repl, text)


def redact(text: str) -> str:
    """Replace anything that looks like a provider API key or card PAN with a
    redaction marker."""
    text = _KEY_RE.sub(_REDACTED, text)
    text = _BILLING_KEY_RE.sub(_REDACTED, text)
    text = _TG_BOT_TOKEN_RE.sub(_REDACTED, text)
    text = _redact_pan(text)
    return text


def redact_obj(obj: object) -> object:
    """Recursively redact strings inside dicts/lists/tuples; pass through scalars."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    return obj


class RedactingFilter(logging.Filter):
    """Logging filter that redacts any ``sk-...`` token from the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )  # type: ignore[assignment]
        except Exception:  # never let redaction itself kill logging
            pass
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the redacting filter to ``logger`` (defaults to the root logger)."""
    target = logger or logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())
