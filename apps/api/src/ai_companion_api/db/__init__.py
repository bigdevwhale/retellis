"""Database layer — SQLAlchemy 2 async + pgvector.

Phase 3 ships the models + Alembic migrations so ``alembic upgrade head`` against
the compose Postgres is real. The running app uses ``InMemoryStore`` by default
(single-user MVP, zero-config) and switches to ``PostgresStore`` when
``COMPANION_USE_DB=1`` — see ``memory/store.py``.
"""

from .base import Base
from .models import Event, Persona, Provider, Usage

__all__ = ["Base", "Event", "Persona", "Provider", "Usage"]
