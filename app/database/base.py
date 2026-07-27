"""Declarative Base und wiederverwendbare Spalten-Mixins."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import JSON, DateTime, MetaData, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Einheitliche Benennung von Constraints — Voraussetzung fuer saubere Alembic-Diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Geldbetraege und Preise: 8 Dezimalstellen genuegen fuer alle gaengigen Krypto-Ticks.
PRICE = Numeric(24, 8)
BIG_AMOUNT = Numeric(28, 8)
RATIO = Numeric(10, 4)
SCORE = Numeric(6, 2)
WEIGHT = Numeric(6, 4)

#: Ergaenzende, unstrukturierte Felder.
#:
#: Produktiv laeuft die Anwendung auf PostgreSQL, wo JSONB indizierbar ist. Die
#: SQLite-Variante existiert nur, damit Repository-Tests ohne Datenbankserver
#: laufen koennen; sie wird nie produktiv verwendet.
JSON_COLUMN = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict] = {
        Decimal: PRICE,
    }


class TimestampMixin:
    """Erstellungs- und Aenderungszeitpunkt, serverseitig in UTC gesetzt."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class CreatedAtMixin:
    """Nur Erstellungszeitpunkt — fuer unveraenderliche Datensaetze wie Events."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
