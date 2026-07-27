"""Redaction: no ``sk-...`` key material may survive into logs or surfaced strings."""

from __future__ import annotations

import io
import logging

from ai_companion_api.observability.redaction import RedactingFilter, redact, redact_obj


def test_redact_openai_key() -> None:
    out = redact("call failed with key sk-proj-aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV3a2f")
    assert "3a2f" not in out
    assert "sk-proj-••••REDACTED••••" in out
    assert "aB1cD2eF" not in out


def test_redact_anthropic_and_openrouter() -> None:
    a = redact("sk-ant-api03-XYZabcdefghijklmnop1234567890")
    assert "XYZabcdefghijklmnop" not in a
    o = redact("sk-or-v1-0123456789abcdefQWERTY")
    assert "QWERTY" not in o


def test_redact_obj_recursive() -> None:
    out = redact_obj(
        {"provider": "openai", "key": "sk-1234567890abcdef", "nested": ["sk-ant-AAAABBBBCCCCDDDD"]}
    )
    assert "sk-1234567890abcdef" not in str(out)
    assert "AAAA" not in str(out)


def test_log_filter_scrubs_record(caplog) -> None:
    logger = logging.getLogger("ai_companion_api.test_redaction")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="ai_companion_api.test_redaction"):
        logger.info("using key sk-proj-SUPERSECRET1234567890ab")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SUPERSECRET" not in joined
    assert "sk-proj-••••REDACTED••••" in joined


def test_no_sk_in_arbitrary_log_stream() -> None:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(RedactingFilter())
    logger = logging.getLogger("ai_companion_api.test_stream")
    logger.handlers.clear()
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    logger.warning("provider auth failed for sk-or-v1-SECRETKEYVALUE12345")
    assert "SECRETKEYVALUE" not in buf.getvalue()
    assert "sk-or-" in buf.getvalue()  # prefix retained, secret erased


# --- I19: non-sk- key shapes (Google AIza, Ollama Cloud / bearer tokens) ---


def test_redact_google_aiza_key() -> None:
    # Google API keys: "AIza" + 35 base64-ish chars.
    out = redact("google call with key AIzaSyA1234567890abcdefghijklmnopqrstuvwxyz")
    assert "SyA1234567890abcdefghijklmnopqrstuvwxyz" not in out
    assert "AIza••••REDACTED••••" in out


def test_redact_bearer_token() -> None:
    # Ollama Cloud (and any bearer-auth provider) ride an Authorization header.
    out = redact("Authorization: Bearer abcdef1234567890ghijklmnopqrstuvwxyz0123456789")
    assert "abcdef1234567890ghijklmnopqrstuvwxyz0123456789" not in out
    assert "Bearer ••••REDACTED••••" in out


def test_redact_keeps_sk_prefix_visible_for_all_variants() -> None:
    # The prefix is retained so operators can see WHICH key kind leaked.
    for prefix in ("sk-", "sk-ant-", "sk-or-", "sk-proj-"):
        out = redact(f"{prefix}SUPERSECRETVALUE1234567890abcd")
        assert prefix in out
        assert "SUPERSECRETVALUE" not in out


def test_redact_does_not_clobber_uuids() -> None:
    # Honest-limit guard: a generic high-entropy backstop would redact the
    # 32-char hex UUIDs that litter our logs (event ids, user ids). The
    # shape-based redactor must leave them alone.
    uuidish = "00000000-0000-0000-0000-000000000000"
    out = redact(f"event id {uuidish} for user a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6")
    assert uuidish in out
    assert "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6" in out


# --- Billing: provider tokens + card PANs (Paddle / ЮKassa) ---


def test_redact_paddle_token() -> None:
    # Paddle API keys carry the ``pdl_`` / ``paddle_`` prefix.
    out = redact("paddle key paddle_live_abcdef0123456789ghijklmnopqrstuv")
    assert "abcdef0123456789ghijklmnopqrstuv" not in out
    assert "paddle_••••REDACTED••••" in out
    out2 = redact("pdl key pdl_live_0123456789abcdefghijklmnopqrstuvwx")
    assert "0123456789abcdefghijklmnopqrstuvwx" not in out2
    assert "pdl_••••REDACTED••••" in out2


def test_redact_yukassa_token() -> None:
    out = redact("yukassa secret yukassa_live_0123456789abcdefghijklmnopqrstuv")
    assert "0123456789abcdefghijklmnopqrstuv" not in out
    assert "yukassa_••••REDACTED••••" in out


def test_redact_prodamus_token() -> None:
    out = redact("prodamus secret prodamus_live_0123456789abcdefghijklmnopqrstuv")
    assert "0123456789abcdefghijklmnopqrstuv" not in out
    assert "prodamus_••••REDACTED••••" in out


def test_redact_billing_prefix_keeps_config_field_names() -> None:
    # The 20-char minimum means config field names that share the prefix are
    # NOT redacted — operators can still read "paddle_environment=sandbox".
    out = redact("paddle_environment=sandbox yukassa_shop_id=123456 prodamus_payform_url=https://x")
    assert "paddle_environment=sandbox" in out
    assert "yukassa_shop_id=123456" in out
    assert "prodamus_payform_url=https://x" in out


def test_redact_card_pan_luhn_valid() -> None:
    # 4242424242424242 is the canonical Stripe test card (Luhn-valid), here as
    # a contiguous run (the shape the contiguous-only PAN matcher catches).
    out = redact("card 4242424242424242 charged")
    assert "4242424242424242" not in out
    assert "••••REDACTED••••" in out


def test_redact_card_pan_contiguous_only() -> None:
    # Contiguous 16-digit PAN is redacted; a dashed PAN is NOT (a dashed run is
    # the shape of a UUID, and an all-zero UUID is Luhn-valid — separator-
    # tolerance would clobber the UUIDs in our logs). Honest limitation: a
    # spaced/dashed PAN survives and relies on the never-logged path instead.
    out = redact("pan 4242424242424242 and 4242-4242-4242-4242")
    assert "4242424242424242" not in out
    assert "4242-4242-4242-4242" in out  # dashed form kept (UUID-shaped)


def test_redact_does_not_clobber_non_luhn_digit_runs() -> None:
    # A 14-digit timestamp must survive — it's not a card number (Luhn fails).
    ts = "20260716101200"
    out = redact(f"event at {ts} for order 123456789012345")
    assert ts in out  # timestamp not redacted
    # 123456789012345 is 15 digits but not Luhn-valid → kept.
    assert "123456789012345" in out
