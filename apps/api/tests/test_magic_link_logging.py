"""The magic-link token is a login credential — it must never reach logs.

``ConsoleEmailTransport`` prints the link to stdout (the dev affordance that
makes the console transport usable — click the link to sign in), but it must
NOT log the link: the ``?token=…`` is a sealed login credential with no
``sk-``/``AIza``/``Bearer`` shape, so ``redaction.RedactingFilter`` would not
scrub it. Structured logs / Langfuse metadata carrying the token = a login
link sitting in observability. The ``logger.info`` line therefore logs only
the recipient.
"""

from __future__ import annotations

import logging

from ai_companion_api.auth.backends.magic_link import ConsoleEmailTransport
from ai_companion_api.observability.redaction import RedactingFilter


async def test_console_transport_does_not_log_token(caplog) -> None:
    token = "SECRETSEALEDLOGINVALUE1234567890"
    link = f"https://app.example.com/v1/auth/magiclink/verify?token={token}"
    transport = ConsoleEmailTransport()
    logger = logging.getLogger("ai_companion_api.auth.backends.magic_link")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="ai_companion_api.auth.backends.magic_link"):
        await transport.send(to="user@example.com", link=link)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    # The sealed login token must not survive into structured logs.
    assert token not in joined
    # The full token-bearing URL must not be logged either.
    assert "token=" not in joined
    # The recipient IS logged (so operators can see a link was issued).
    assert "user@example.com" in joined
