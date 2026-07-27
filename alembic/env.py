"""Alembic-Umgebung.

Die Datenbank-URL kommt aus den Anwendungssettings, damit keine Zugangsdaten in
``alembic.ini`` stehen. Migrationen laufen synchron ueber ``psycopg`` — das ist
robuster als eine asynchrone Migration und im Deployment ueblich.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.database.base import Base

# Der Import registriert alle Modelle bei Base.metadata.
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Sync-DSN. Alembic laeuft nicht asynchron."""
    settings = get_settings()
    # asyncpg kann Alembic nicht bedienen; psycopg ist der Sync-Treiber.
    return settings.sync_database_url


def run_migrations_offline() -> None:
    """SQL-Skripte erzeugen, ohne eine Verbindung aufzubauen."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migrationen gegen die Datenbank ausfuehren."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
