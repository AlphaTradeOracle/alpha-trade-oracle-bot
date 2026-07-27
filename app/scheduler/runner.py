"""APScheduler-Einbindung.

APScheduler wurde gegenueber Celery gewaehlt, weil das MVP nur periodische Jobs
ohne verteilte Queue benoetigt. Die Jobs sind als eigenstaendige Service-Aufrufe
formuliert, sodass ein spaeterer Wechsel zu Celery keine Fachlogik beruehrt.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.database.session import session_scope
from app.repositories.event_repository import ScheduledJobRepository
from app.scheduler.jobs import market_scan_job, run_market_scan
from app.services.scan_service import ScanService

logger = get_logger(__name__)


class SchedulerRunner:
    """Verwaltet den Lebenszyklus des Schedulers."""

    def __init__(self, scan_service: ScanService, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._scan_service = scan_service
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    async def start(self) -> None:
        if not self._settings.enable_scheduler:
            logger.info("scheduler_disabled_by_config")
            return

        definition = market_scan_job(self._settings.scan_interval_minutes)

        async with session_scope() as session:
            await ScheduledJobRepository(session).register(
                definition.key, definition.job_type, definition.interval_seconds
            )

        self._scheduler.add_job(
            run_market_scan,
            trigger=IntervalTrigger(seconds=definition.interval_seconds),
            args=[self._scan_service, definition.key],
            id=definition.key,
            name=definition.description,
            # Verpasste Ausfuehrungen werden nicht nachgeholt, sondern zusammengefasst.
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )

        self._scheduler.start()
        logger.info(
            "scheduler_started",
            job_key=definition.key,
            interval_minutes=self._settings.scan_interval_minutes,
        )

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("scheduler_stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._scheduler.running)
