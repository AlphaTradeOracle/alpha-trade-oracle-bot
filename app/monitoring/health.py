"""Health- und Readiness-Checks.

Unterscheidung:
- ``/health`` (Liveness) prueft nur, ob der Prozess arbeitet. Er darf nicht von
  externen Systemen abhaengen, sonst wuerde ein Redis-Ausfall einen Neustart
  durch den Orchestrator ausloesen.
- ``/ready`` (Readiness) prueft die Abhaengigkeiten, die fuer die Bearbeitung von
  Anfragen zwingend sind.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.database.redis_client import check_redis_connection
from app.database.session import check_database_connection

logger = get_logger(__name__)

#: Zeitlimit je Einzelpruefung, damit ein haengendes System den Check nicht blockiert.
CHECK_TIMEOUT_SECONDS = 5.0


@dataclass
class ComponentStatus:
    """Status einer einzelnen Komponente."""

    name: str
    healthy: bool
    #: ``True`` bedeutet: ein Ausfall verhindert die Bereitschaft.
    required: bool
    detail: str = ""


@dataclass
class HealthReport:
    """Gesamtergebnis eines Readiness-Checks."""

    components: list[ComponentStatus] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        """Bereit, wenn alle als erforderlich markierten Komponenten laufen."""
        return all(component.healthy for component in self.components if component.required)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.is_ready else "not_ready",
            "checked_at": utc_now().isoformat(),
            "components": {
                component.name: {
                    "healthy": component.healthy,
                    "required": component.required,
                    "detail": component.detail,
                }
                for component in self.components
            },
        }


class HealthService:
    """Fuehrt alle Verbindungspruefungen nebenlaeufig aus."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        market_data_check: Any | None = None,
        telegram_check: Any | None = None,
        llm_check: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._market_data_check = market_data_check
        self._telegram_check = telegram_check
        self._llm_check = llm_check

    async def liveness(self) -> dict[str, Any]:
        """Reine Prozesspruefung ohne externe Abhaengigkeiten."""
        return {
            "status": "ok",
            "app": self._settings.app_name,
            "version": self._settings.app_version,
            "environment": self._settings.app_env,
            "timestamp": utc_now().isoformat(),
        }

    async def readiness(self) -> HealthReport:
        report = HealthReport()

        checks: list[tuple[str, Any, bool]] = [
            ("database", check_database_connection, True),
            ("redis", check_redis_connection, False),
        ]
        if self._market_data_check is not None:
            checks.append(("market_data", self._market_data_check, True))
        if self._telegram_check is not None:
            checks.append(("telegram", self._telegram_check, False))
        if self._llm_check is not None and self._settings.llm_configured:
            checks.append(("llm", self._llm_check, False))

        results = await asyncio.gather(*(_run_check(name, check) for name, check, _ in checks))

        for (name, _, required), (healthy, detail) in zip(checks, results, strict=True):
            report.components.append(
                ComponentStatus(name=name, healthy=healthy, required=required, detail=detail)
            )

        if not report.is_ready:
            failed = [c.name for c in report.components if c.required and not c.healthy]
            logger.warning("readiness_check_failed", failed_components=failed)

        return report


async def _run_check(name: str, check: Any) -> tuple[bool, str]:
    """Einzelpruefung mit Zeitlimit ausfuehren; Fehler werden nie durchgereicht."""
    try:
        healthy = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        return bool(healthy), "" if healthy else "Pruefung war nicht erfolgreich"
    except TimeoutError:
        return False, f"Zeitlimit von {CHECK_TIMEOUT_SECONDS:.0f}s ueberschritten"
    except Exception as exc:
        logger.debug("health_check_error", component=name, error=str(exc))
        return False, str(exc)[:200]
