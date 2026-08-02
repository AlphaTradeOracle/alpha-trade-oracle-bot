"""APScheduler-Einbindung.

APScheduler wurde gegenueber Celery gewaehlt, weil das MVP nur periodische Jobs
ohne verteilte Queue benoetigt. Die Jobs sind als eigenstaendige Service-Aufrufe
formuliert, sodass ein spaeterer Wechsel zu Celery keine Fachlogik beruehrt.
"""

from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.bot.notifier import TelegramNotifier
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.repositories.event_repository import ScheduledJobRepository
from app.scheduler.jobs import (
    JobDefinition,
    market_scan_job,
    paper_digest_job,
    paper_update_job,
    run_market_scan,
    run_paper_digest,
    run_paper_update,
    run_universe_refresh,
    universe_refresh_job,
)
from app.services.data_retention_service import DataRetentionService
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService
from app.services.universe_service import UniverseService
from app.market_data.base import MarketDataProvider

logger = get_logger(__name__)


def _next_run_time(next_run_at: datetime | None) -> datetime:
    """APScheduler-Startzeit aus DB-``next_run_at`` ableiten.

    ``IntervalTrigger`` ohne ``next_run_time`` plant den *ersten* Lauf erst nach
    einem vollen Intervall — bei 24h-Jobs wuerde ein Worker-Restart den
    Universe-Refresh damit tagelang ueberspringen. Ueberfaellige Jobs starten
    deshalb sofort.
    """
    now = utc_now()
    if next_run_at is None:
        return now
    due = ensure_utc(next_run_at)
    return now if due <= now else due


class SchedulerRunner:
    """Verwaltet den Lebenszyklus des Schedulers."""

    def __init__(
        self,
        scan_service: ScanService,
        settings: Settings | None = None,
        *,
        universe_service: UniverseService | None = None,
        data_retention: DataRetentionService | None = None,
        paper_trading: PaperTradingService | None = None,
        provider: MarketDataProvider | None = None,
        providers: dict[str, MarketDataProvider] | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._scan_service = scan_service
        self._universe_service = universe_service
        self._data_retention = data_retention
        self._paper = paper_trading
        self._provider = provider
        self._providers = providers or {}
        self._notifier = notifier
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
        digest_definition = None
        if (
            self._paper is not None
            and self._provider is not None
            and self._settings.enable_paper_trading
        ):
            paper_definition = paper_update_job(self._settings.paper_update_interval_minutes)
            definitions.append(paper_definition)
            if (
                self._notifier is not None
                and self._settings.paper_hourly_digest_enabled
            ):
                digest_definition = paper_digest_job(
                    self._settings.paper_digest_interval_minutes
                )
                definitions.append(digest_definition)

        next_runs: dict[str, datetime] = {}
        async with session_scope() as session:
            jobs = ScheduledJobRepository(session)
            for definition in definitions:
                row = await jobs.register(
                    definition.key, definition.job_type, definition.interval_seconds
                )
                next_runs[definition.key] = _next_run_time(row.next_run_at)

            if not self._settings.paper_hourly_digest_enabled:
                disabled = await jobs.disable_job_types(
                    {"paper_digest"},
                    reason="disabled: desk website is the status surface",
                )
                if disabled:
                    logger.info("scheduler_jobs_disabled", job_keys=disabled)

        self._add_interval_job(
            run_market_scan,
            scan_definition,
            args=[self._scan_service, scan_definition.key],
            next_run_time=next_runs[scan_definition.key],
            misfire_grace_time=300,
        )

        if self._universe_service is not None and self._settings.enable_universe_scan:
            refresh_definition = universe_refresh_job(self._settings.universe_refresh_hours)
            self._add_interval_job(
                run_universe_refresh,
                refresh_definition,
                kwargs={
                    "universe_service": self._universe_service,
                    "job_key": refresh_definition.key,
                    "data_retention": self._data_retention,
                },
                next_run_time=next_runs[refresh_definition.key],
                misfire_grace_time=600,
            )

        if paper_definition is not None and self._paper is not None and self._provider is not None:
            self._add_interval_job(
                run_paper_update,
                paper_definition,
                kwargs={
                    "paper": self._paper,
                    "provider": self._provider,
                    "job_key": paper_definition.key,
                    "providers": self._providers,
                },
                next_run_time=next_runs[paper_definition.key],
                misfire_grace_time=120,
            )

        if (
            digest_definition is not None
            and self._paper is not None
            and self._provider is not None
            and self._notifier is not None
        ):
            self._add_interval_job(
                run_paper_digest,
                digest_definition,
                kwargs={
                    "paper": self._paper,
                    "provider": self._provider,
                    "notifier": self._notifier,
                    "job_key": digest_definition.key,
                    "providers": self._providers,
                },
                next_run_time=next_runs[digest_definition.key],
                misfire_grace_time=300,
            )

        self._scheduler.start()
        logger.info(
            "scheduler_started",
            jobs=[d.key for d in definitions],
            interval_minutes=self._settings.scan_interval_minutes,
            next_runs={key: value.isoformat() for key, value in next_runs.items()},
        )

    def _add_interval_job(
        self,
        func: object,
        definition: JobDefinition,
        *,
        next_run_time: datetime,
        misfire_grace_time: int,
        args: list[object] | None = None,
        kwargs: dict[str, object] | None = None,
    ) -> None:
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=definition.interval_seconds),
            args=args or [],
            kwargs=kwargs or {},
            id=definition.key,
            name=definition.description,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=misfire_grace_time,
            next_run_time=next_run_time,
        )

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("scheduler_stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._scheduler.running)
