"""Definition der Hintergrundjobs.

Jeder Job ist idempotent: er beansprucht ueber
:class:`~app.repositories.event_repository.ScheduledJobRepository` sein
Ausfuehrungsrecht. Startet ein zweiter Worker, faellt dessen Aufruf durch, weil
``next_run_at`` noch in der Zukunft liegt.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import EventSeverity
from app.core.logging import get_logger, set_correlation_id
from app.database.session import session_scope
from app.repositories.event_repository import EventRepository, ScheduledJobRepository
from app.services.scan_service import ScanService

logger = get_logger(__name__)

#: Verfuegbare Scan-Intervalle in Minuten.
SCAN_INTERVALS: dict[str, int] = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


@dataclass(frozen=True)
class JobDefinition:
    """Beschreibung eines periodischen Jobs."""

    key: str
    job_type: str
    interval_seconds: int
    description: str


def market_scan_job(interval_minutes: int) -> JobDefinition:
    return JobDefinition(
        key=f"market_scan:{interval_minutes}m",
        job_type="market_scan",
        interval_seconds=interval_minutes * 60,
        description=f"Marktscan alle {interval_minutes} Minuten",
    )


async def run_market_scan(scan_service: ScanService, job_key: str) -> None:
    """Marktscan ausfuehren, sofern das Ausfuehrungsrecht beansprucht werden kann."""
    set_correlation_id()

    async with session_scope() as session:
        jobs = ScheduledJobRepository(session)
        claimed = await jobs.claim(job_key)

    if not claimed:
        logger.debug("job_skipped_not_due", job_key=job_key)
        return

    logger.info("job_started", job_key=job_key)

    try:
        async with session_scope() as session:
            result = await scan_service.scan(session)
        success = True
        error: str | None = None
        summary = result.as_summary()
    except Exception as exc:
        success = False
        error = str(exc)
        summary = {}
        logger.error("job_failed", job_key=job_key, error=error, exc_info=True)

    async with session_scope() as session:
        await ScheduledJobRepository(session).complete(job_key, success=success, error=error)
        if not success:
            await EventRepository(session).record(
                "scheduled_job_failed",
                f"Job {job_key} ist fehlgeschlagen: {error}",
                severity=EventSeverity.ERROR,
            )

    if success:
        logger.info("job_completed", job_key=job_key, **summary)
