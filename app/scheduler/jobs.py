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
from app.market_data.base import MarketDataProvider
from app.repositories.event_repository import EventRepository, ScheduledJobRepository
from app.repositories.paper_repository import PaperRepository
from app.services.paper_trading_service import PaperTradingService
from app.services.scan_service import ScanService
from app.services.universe_service import UniverseService

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


def universe_refresh_job(interval_hours: int) -> JobDefinition:
    return JobDefinition(
        key=f"universe_refresh:{interval_hours}h",
        job_type="universe_refresh",
        interval_seconds=interval_hours * 3600,
        description=f"Universe-Refresh alle {interval_hours} Stunden",
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


async def run_universe_refresh(universe_service: UniverseService, job_key: str) -> None:
    """Universe-Refresh ausfuehren, sofern das Ausfuehrungsrecht beansprucht werden kann."""
    set_correlation_id()

    async with session_scope() as session:
        claimed = await ScheduledJobRepository(session).claim(job_key)

    if not claimed:
        logger.debug("job_skipped_not_due", job_key=job_key)
        return

    logger.info("job_started", job_key=job_key)

    try:
        async with session_scope() as session:
            result = await universe_service.refresh(session)
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
        else:
            await EventRepository(session).record(
                "universe_refresh_completed",
                "Universe-Refresh abgeschlossen.",
                severity=EventSeverity.INFO,
                payload=summary,
            )

    if success:
        logger.info("job_completed", job_key=job_key, **summary)


def paper_update_job(interval_minutes: int) -> JobDefinition:
    return JobDefinition(
        key=f"paper_update:{interval_minutes}m",
        job_type="paper_update",
        interval_seconds=interval_minutes * 60,
        description=f"Paper-Positionen aktualisieren alle {interval_minutes} Minuten",
    )


async def run_paper_update(
    paper: PaperTradingService,
    provider: MarketDataProvider,
    job_key: str,
    *,
    providers: dict[str, MarketDataProvider] | None = None,
) -> None:
    """Pending Retest-Entries aufloesen und offene Positionen gegen Kurse pruefen."""
    set_correlation_id()

    async with session_scope() as session:
        claimed = await ScheduledJobRepository(session).claim(job_key)

    if not claimed:
        logger.debug("job_skipped_not_due", job_key=job_key)
        return

    logger.info("job_started", job_key=job_key)

    try:
        async with session_scope() as session:
            pending_summary: dict[str, int] = {"filled": 0, "skipped": 0, "still_pending": 0}
            if paper.retest_enabled:
                resolve = await paper.resolve_pending_retest(session, provider)
                pending_summary = {
                    "filled": resolve.filled,
                    "skipped": resolve.skipped,
                    "still_pending": resolve.still_pending,
                }

            account = await paper.get_or_create_account(session)
            open_positions = await PaperRepository(session).list_open_positions(account.id)
            symbols = [position.symbol for position in open_positions]
            if not symbols:
                summary: dict[str, int] = {
                    "open_positions": 0,
                    "updated": 0,
                    **{f"retest_{k}": v for k, v in pending_summary.items()},
                }
            else:
                prices = await _collect_prices(provider, symbols, providers=providers)
                updated = await paper.update_open_positions(session, prices)
                summary = {
                    "open_positions": len(open_positions),
                    "prices": len(prices),
                    "updated": len(updated),
                    **{f"retest_{k}": v for k, v in pending_summary.items()},
                }
        success = True
        error: str | None = None
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


async def _collect_prices(
    primary: MarketDataProvider,
    symbols: list[str],
    *,
    providers: dict[str, MarketDataProvider] | None = None,
) -> dict[str, float]:
    """Kurse vom Primaer-Provider laden; fehlende Symbole bei anderen Providern nachziehen."""
    wanted = [symbol.upper() for symbol in symbols]
    prices: dict[str, float] = {}
    try:
        prices.update(await primary.get_prices(wanted))
    except Exception as exc:
        logger.warning("paper_primary_prices_failed", error=str(exc))

    missing = [symbol for symbol in wanted if symbol not in prices]
    if not missing:
        return prices

    candidates: list[MarketDataProvider] = [primary]
    for provider in (providers or {}).values():
        if provider is not primary and provider not in candidates:
            candidates.append(provider)

    for symbol in missing:
        for provider in candidates:
            try:
                prices[symbol] = await provider.get_price(symbol)
                break
            except Exception:
                continue

    return prices
