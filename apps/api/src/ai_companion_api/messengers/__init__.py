"""Per-user external-messenger links (Telegram first).

The package is adapter-agnostic on purpose: ``store.py`` + ``polling.py`` know
nothing about Telegram specifics — those live in ``telegram/`` behind the
``MessengerAdapter`` Protocol in ``base.py``. Adding WhatsApp/Signal later =
a new subpackage + one ``registry.py`` entry.
"""
