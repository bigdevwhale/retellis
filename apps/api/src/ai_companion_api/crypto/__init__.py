"""Server-side crypto helpers (envelope encryption for messenger bot tokens).

This is deliberately separate from ``vault/`` (the zero-knowledge client-side
BYOK scheme). The envelope here is **server-managed**: the server holds the data
encryption key (``MESSENGER_TOKEN_DEK`` env) and can decrypt. It exists so a
leaked DB dump does not expose live Telegram bot tokens — not to make the server
blind. The honest-limits contract: never claim this is zero-knowledge.
"""
