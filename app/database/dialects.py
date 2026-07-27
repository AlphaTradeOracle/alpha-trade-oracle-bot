"""Dialektabhaengige SQL-Konstrukte.

Produktiv laeuft die Anwendung auf PostgreSQL. Repository-Tests nutzen SQLite,
damit sie ohne laufenden Datenbankserver ausfuehrbar sind. Beide Dialekte
beherrschen ``INSERT ... ON CONFLICT DO NOTHING``, die Konstrukte liegen aber in
unterschiedlichen Modulen und sind nicht ueber das generische ``insert()``
erreichbar.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable
from sqlalchemy.sql.schema import Table


def insert_ignore_duplicates(
    session: AsyncSession,
    table: Any,
    values: list[dict[str, Any]] | dict[str, Any],
    *,
    index_elements: list[str],
) -> Executable:
    """``INSERT`` erzeugen, das vorhandene Zeilen still ueberspringt.

    Macht wiederholte Importe unschaedlich — wichtig, weil sich Scans zeitlich
    ueberlappen und dieselben Kerzen erneut liefern koennen.
    """
    target: Table | Any = table
    module = sqlite if _dialect_name(session) == "sqlite" else postgresql
    return (
        module.insert(target).values(values).on_conflict_do_nothing(index_elements=index_elements)
    )


def _dialect_name(session: AsyncSession) -> str:
    """Dialekt der Session ermitteln; im Zweifel PostgreSQL annehmen."""
    try:
        return str(session.get_bind().dialect.name)
    except Exception:
        return "postgresql"


__all__ = ["insert_ignore_duplicates"]
