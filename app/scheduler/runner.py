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
from app.scheduler.jobs import (
    market_scan_job,
    paper_update_job,
    run_market_scan,
    run_paper_update,
    run_universe_refresh,
    universe_refresh_job,
)
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService
from app.services.universe_service import UniverseService
from app.market_data.base import MarketDataProvider

logger = get_logger(__name__)


class SchedulerRunner:
    """Verwaltet den Lebenszyklus des Schedulers."""

    def __init__(
        self,
        scan_service: ScanService,
        settings: Settings | None = None,
        *,
        universe_service: UniverseService | None = None,
        paper_trading: PaperTradingService | None = None,
        provider: MarketDataProvider | None = None,
        providers: dict[str, MarketDataProvider] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._scan_service = scan_service
        self._universe_service = universe_service
        self._paper = paper_trading
        self._provider = provider
        self._providers = providers or {}
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    async def start(self) -> None:
        if not self._settings.enable_scheduler:
            logger.info("scheduler_disabled_by_config")
            return

        scan_definition = market_scan_job(self._settings.scan_interval_minutes)
        definitions = [scan_definition]

        if self._universe_service is not None and self._settings.enable_universe_scan:
            definitions.append(universe_refresh_job(self._settings.universe_refresh_hours))

        paper_definition = None
        if (
            self._paper is not None
            and self._provider is not None
            and self._settings.enable_paper_trading
        ):
            paper_definition = paper_update_job(self._settings.paper_update_interval_minutes)
            definitions.append(paper_definition)

        async with session_scope() as session:
            jobs = ScheduledJobRepository(session)
            for definition in definitions:
                await jobs.register(
                    definition.key, definition.job_type, definition.interval_seconds
                )

        self._scheduler.add_job(
            run_market_scan,
            trigger=IntervalTrigger(seconds=scan_definition.interval_seconds),
            args=[self._scan_service, scan_definition.key],
            id=scan_definition.key,
            name=scan_definition.description,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )

        if self._universe_service is not None and self._settings.enable_universe_scan:
            refresh_definition = universe_refresh_job(self._settings.universe_refresh_hours)
            self._scheduler.add_job(
                run_universe_refresh,
                trigger=IntervalTrigger(seconds=refresh_definition.interval_seconds),
                args=[self._universe_service, refresh_definition.key],
                id=refresh_definition.key,
                name=refresh_definition.description,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=600,
            )

        if paper_definition is not None and self._paper is not None and self._provider is not None:
            self._scheduler.add_job(
                run_paper_update,
                trigger=IntervalTrigger(seconds=paper_definition.interval_seconds),
                kwargs={
                    "paper": self._paper,
                    "provider": self._provider,
                    "job_key": paper_definition.key,
                    "providers": self._providers,
                },
                id=paper_definition.key,
                name=paper_definition.description,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
            )

        self._scheduler.start()
        logger.info(
            "scheduler_started",
            jobs=[d.key for d in definitions],
            interval_minutes=self._settings.scan_interval_minutes,
        )

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("scheduler_stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._scheduler.running)
