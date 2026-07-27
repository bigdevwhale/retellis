"""Alembic env — sync engine derived from DATABASE_URL.

The app runs on asyncpg, but Alembic migrations run synchronously. We swap the
async driver for psycopg v3 (``postgresql+asyncpg://`` → ``postgresql+psycopg://``)
so a single ``DATABASE_URL`` env var drives both — asyncpg has no sync mode. ``target_metadata`` is the
SQLAlchemy ``Base.metadata`` from ``ai_companion_api.db`` so autogenerate works.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``ai_companion_api`` importable when alembic runs from the apps/api dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_companion_api.db import (
    Base,  # noqa: E402
    models,  # noqa: E402,F401  (registers tables on Base)
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Fall back to the compose default so `alembic upgrade head` works in docker.
        url = "postgresql+asyncpg://companion:companion@postgres:5432/companion"
    # Swap the async driver for a sync one Alembic can drive. Use psycopg v3
    # (``postgresql+psycopg://``) — asyncpg has no sync mode and the bare
    # ``postgresql://`` default would pull in psycopg2, which isn't installed.
    return url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
