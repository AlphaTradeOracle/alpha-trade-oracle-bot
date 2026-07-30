"""Tiefe OHLCV-Historie fuer das Universe nachladen.

Im Unterschied zu ``python -m app.cli data backfill`` arbeitet dieses Skript in
kleinen Zeitfenstern, schreibt nach jedem Fenster in die Datenbank und haelt den
Fortschritt in einer State-Datei fest. Ein abgebrochener Lauf (z. B. weil der
Worker-Container neu gebaut wurde) kann damit ohne Datenverlust fortgesetzt
werden.

Beispiele::

    python -m scripts.backfill_candles --timeframe 1h --days 190
    python -m scripts.backfill_candles -t 4h --days 400 --concurrency 4
    python -m scripts.backfill_candles -t 1h --symbols BTCUSDT,ETHUSDT --no-resume

Kerzen werden ueber ``ON CONFLICT DO NOTHING`` geschrieben: bestehende Zeilen
bleiben unveraendert, wiederholte Laeufe erzeugen keine Duplikate.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.errors import RateLimitError, SymbolNotFoundError
from app.core.logging import configure_logging, get_logger
from app.core.time import timeframe_to_timedelta, utc_now
from app.database.session import dispose_engine, session_scope
from app.market_data.base import MarketDataProvider
from app.market_data.factory import create_universe_providers
from app.market_data.types import CandleSeries
from app.models.market import Asset, MarketCandle
from app.repositories.asset_repository import AssetRepository

logger = get_logger(__name__)

DEFAULT_STATE_FILE = Path("exports/backfill_candles_state.json")
DEFAULT_REPORT_FILE = Path("exports/backfill_candles_report.json")

#: Maximale Kerzen pro Provider-Request. Ein Fenster bleibt bewusst darunter,
#: damit jede Abfrage genau eine Seite ist und der Fortschritt vorhersagbar bleibt.
PROVIDER_PAGE_SIZE: dict[str, int] = {
    "kucoin": 1400,
    "binance": 900,
    "coinbase": 300,
}
FALLBACK_PAGE_SIZE = 300

#: So viele aufeinanderfolgende leere Fenster gelten als "vor dem Listing".
EMPTY_WINDOWS_BEFORE_STOP = 2

#: Wiederholungen pro Fenster bei Rate-Limit- oder Netzwerkfehlern.
MAX_WINDOW_ATTEMPTS = 5


@dataclass
class TargetState:
    """Ergebnis eines Symbol/Timeframe-Paares, persistiert in der State-Datei."""

    symbol: str
    timeframe: str
    status: str = "pending"
    bars: int = 0
    written: int = 0
    oldest: str | None = None
    newest: str | None = None
    reason: str | None = None
    updated_at: str | None = None


@dataclass
class RunStats:
    targets: int = 0
    skipped_resume: int = 0
    completed: int = 0
    partial: int = 0
    failed: int = 0
    candles_written: int = 0
    requests: int = 0
    started_at: str = field(default_factory=lambda: utc_now().isoformat())


class StateStore:
    """Fortschritt je Symbol/Timeframe als JSON-Datei.

    Wird nach jedem abgeschlossenen Paar geschrieben, damit ein hart beendeter
    Prozess (SIGKILL/OOM) hoechstens das gerade laufende Symbol verliert.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, TargetState] = {}
        self._lock = asyncio.Lock()

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("backfill_state_unreadable", path=str(self._path), error=str(exc))
            return
        for key, value in (raw.get("entries") or {}).items():
            with contextlib.suppress(TypeError):
                self._entries[key] = TargetState(**value)

    def get(self, symbol: str, timeframe: str) -> TargetState | None:
        return self._entries.get(f"{symbol}|{timeframe}")

    async def put(self, state: TargetState) -> None:
        state.updated_at = utc_now().isoformat()
        self._entries[f"{state.symbol}|{state.timeframe}"] = state
        async with self._lock:
            await asyncio.to_thread(self._write)

    def entries(self) -> list[TargetState]:
        return list(self._entries.values())

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": utc_now().isoformat(),
            "entries": {key: asdict(value) for key, value in self._entries.items()},
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


class Backfiller:
    """Laedt Kerzenfenster rueckwaerts und schreibt sie idempotent weg."""

    def __init__(
        self,
        providers: dict[str, MarketDataProvider],
        *,
        settings: Settings,
        state: StateStore,
        stats: RunStats,
        args: argparse.Namespace,
        stop: asyncio.Event,
    ) -> None:
        self._providers = providers
        self._settings = settings
        self._state = state
        self._stats = stats
        self._args = args
        self._stop = stop

    def _provider_for(self, exchange: str) -> tuple[str, MarketDataProvider] | None:
        name = (exchange or "").lower().strip()
        provider = self._providers.get(name)
        if provider is not None:
            return name, provider
        primary = self._settings.market_data_provider.lower().strip()
        provider = self._providers.get(primary)
        if provider is not None:
            return primary, provider
        return next(iter(self._providers.items()), None)

    async def run_target(
        self, asset_id: int, symbol: str, exchange: str, timeframe: str, start: datetime
    ) -> TargetState:
        state = TargetState(symbol=symbol, timeframe=timeframe)
        resolved = self._provider_for(exchange)
        if resolved is None:
            state.status = "failed"
            state.reason = "kein Provider verfuegbar"
            return state

        provider_name, provider = resolved
        interval = timeframe_to_timedelta(timeframe)
        page = PROVIDER_PAGE_SIZE.get(provider_name, FALLBACK_PAGE_SIZE)
        window = interval * page

        cursor_end = utc_now()
        empty_windows = 0
        oldest_seen: datetime | None = None
        newest_seen: datetime | None = None

        while cursor_end > start and not self._stop.is_set():
            window_start = max(start, cursor_end - window)
            series = await self._fetch_window(
                provider, symbol, timeframe, window_start, cursor_end, page, state
            )
            if series is None:
                state.status = "failed"
                return state

            if series.candles:
                empty_windows = 0
                written = await self._persist(asset_id, series)
                state.bars += len(series.candles)
                state.written += written
                self._stats.candles_written += written
                first = series.candles[0].open_time
                last = series.candles[-1].open_time
                oldest_seen = first if oldest_seen is None else min(oldest_seen, first)
                newest_seen = last if newest_seen is None else max(newest_seen, last)
                # Vor die aelteste gelieferte Kerze springen, damit kein Fenster
                # doppelt geladen wird, wenn der Provider frueher endet.
                cursor_end = min(window_start, first) - timedelta(milliseconds=1)
            else:
                empty_windows += 1
                if empty_windows >= EMPTY_WINDOWS_BEFORE_STOP:
                    state.reason = "keine Daten mehr (Listing-Beginn erreicht)"
                    break
                cursor_end = window_start - timedelta(milliseconds=1)

            if self._args.sleep_ms:
                await asyncio.sleep(self._args.sleep_ms / 1000.0)

        state.oldest = oldest_seen.isoformat() if oldest_seen else None
        state.newest = newest_seen.isoformat() if newest_seen else None

        if self._stop.is_set() and cursor_end > start:
            state.status = "interrupted"
        elif oldest_seen is not None and oldest_seen <= start + interval * 2:
            state.status = "complete"
        else:
            state.status = "partial"
            state.reason = state.reason or "Provider liefert keine tiefere Historie"
        return state

    async def _fetch_window(
        self,
        provider: MarketDataProvider,
        symbol: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        page: int,
        state: TargetState,
    ) -> CandleSeries | None:
        """Ein Zeitfenster laden; Rate-Limits werden mit Backoff wiederholt."""
        delay = 1.0
        for attempt in range(1, MAX_WINDOW_ATTEMPTS + 1):
            try:
                self._stats.requests += 1
                return await provider.get_candles(
                    symbol,
                    timeframe,
                    limit=page,
                    start_time=window_start,
                    end_time=window_end,
                )
            except SymbolNotFoundError as exc:
                state.reason = f"Symbol beim Provider unbekannt: {exc}"
                return None
            except RateLimitError as exc:
                wait = exc.retry_after_seconds or delay
                logger.warning(
                    "backfill_rate_limited",
                    symbol=symbol,
                    timeframe=timeframe,
                    attempt=attempt,
                    wait_seconds=round(wait, 1),
                )
                await asyncio.sleep(min(60.0, wait))
                delay = min(60.0, delay * 2)
            except Exception as exc:
                if attempt == MAX_WINDOW_ATTEMPTS:
                    state.reason = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "backfill_window_failed",
                        symbol=symbol,
                        timeframe=timeframe,
                        error=str(exc),
                    )
                    return None
                await asyncio.sleep(min(30.0, delay))
                delay = min(30.0, delay * 2)
        state.reason = "Rate-Limit nach mehreren Versuchen"
        return None

    async def _persist(self, asset_id: int, series: CandleSeries) -> int:
        if self._args.dry_run:
            return 0
        async with session_scope() as session:
            return await AssetRepository(session).upsert_candles(asset_id, series)


async def _load_targets(args: argparse.Namespace) -> list[tuple[int, str, str]]:
    """Assets aus dem aktiven Universe auswaehlen (id, symbol, exchange)."""
    wanted: set[str] | None = None
    if args.symbols:
        wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    if args.symbols_file:
        raw = await asyncio.to_thread(Path(args.symbols_file).read_text, encoding="utf-8")
        parsed = {line.strip().upper() for line in raw.splitlines() if line.strip()}
        wanted = parsed if wanted is None else wanted | parsed

    statement = select(Asset.id, Asset.symbol, Asset.exchange).order_by(
        Asset.market_cap_rank.asc().nulls_last(), Asset.symbol
    )
    if not args.all_assets:
        statement = statement.where(Asset.in_universe.is_(True), Asset.is_active.is_(True))
    if wanted:
        statement = statement.where(Asset.symbol.in_(wanted))
    if args.exchange:
        statement = statement.where(Asset.exchange == args.exchange.lower().strip())
    if args.limit and args.limit > 0:
        statement = statement.limit(args.limit)

    async with session_scope() as session:
        rows = (await session.execute(statement)).all()
    return [(int(row[0]), str(row[1]), str(row[2])) for row in rows]


async def _existing_coverage(
    targets: list[tuple[int, str, str]], timeframes: list[str]
) -> dict[tuple[int, str], tuple[int, datetime | None]]:
    """Vorhandene Bar-Anzahl und aeltesten Zeitstempel je Asset/Timeframe."""
    asset_ids = [asset_id for asset_id, _, _ in targets]
    if not asset_ids:
        return {}
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    MarketCandle.asset_id,
                    MarketCandle.timeframe,
                    func.count().label("bars"),
                    func.min(MarketCandle.open_time).label("oldest"),
                )
                .where(
                    MarketCandle.asset_id.in_(asset_ids),
                    MarketCandle.timeframe.in_(timeframes),
                )
                .group_by(MarketCandle.asset_id, MarketCandle.timeframe)
            )
        ).all()
    return {(int(r[0]), str(r[1])): (int(r[2]), r[3]) for r in rows}


def _should_skip(
    coverage: tuple[int, datetime | None] | None,
    state: TargetState | None,
    *,
    start: datetime,
    interval: timedelta,
    tolerance_bars: int,
) -> str | None:
    """Grund fuer das Ueberspringen, sonst ``None``."""
    if coverage is not None:
        _, oldest = coverage
        if oldest is not None:
            oldest_utc = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
            if oldest_utc <= start + interval * tolerance_bars:
                return "Zieltiefe bereits in der Datenbank"
    if state is not None and state.status in {"complete", "partial"}:
        return f"State-Datei: {state.status}"
    return None


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    timeframes = [tf.strip() for tf in args.timeframe.split(",") if tf.strip()]
    for timeframe in timeframes:
        timeframe_to_timedelta(timeframe)

    start = utc_now() - timedelta(days=args.days)
    state_store = StateStore(Path(args.state_file))
    if args.resume:
        state_store.load()

    targets = await _load_targets(args)
    coverage = await _existing_coverage(targets, timeframes)

    stats = RunStats(targets=len(targets) * len(timeframes))
    stop = asyncio.Event()
    _install_signal_handlers(stop)

    # Kein Redis: der Candle-Cache wuerde Fenster-Abfragen verfaelschen.
    providers = create_universe_providers(settings)
    backfiller = Backfiller(
        providers, settings=settings, state=state_store, stats=stats, args=args, stop=stop
    )

    print(
        f"Backfill: {len(targets)} Assets x {len(timeframes)} Timeframes "
        f"({args.timeframe}), Ziel ab {start.date().isoformat()} "
        f"({args.days} Tage), Provider {sorted(providers)}",
        flush=True,
    )

    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    counter = {"done": 0}
    total = len(targets)
    t0 = time.monotonic()

    async def process(asset_id: int, symbol: str, exchange: str) -> None:
        async with semaphore:
            for timeframe in timeframes:
                if stop.is_set():
                    return
                interval = timeframe_to_timedelta(timeframe)
                skip = (
                    _should_skip(
                        coverage.get((asset_id, timeframe)),
                        state_store.get(symbol, timeframe),
                        start=start,
                        interval=interval,
                        tolerance_bars=args.tolerance_bars,
                    )
                    if args.resume
                    else None
                )
                if skip:
                    stats.skipped_resume += 1
                    continue

                result = await backfiller.run_target(asset_id, symbol, exchange, timeframe, start)
                await state_store.put(result)
                if result.status == "complete":
                    stats.completed += 1
                elif result.status == "partial":
                    stats.partial += 1
                elif result.status == "failed":
                    stats.failed += 1

                counter["done"] += 1
                elapsed = time.monotonic() - t0
                print(
                    f"[{counter['done']}/{total * len(timeframes)}] {symbol} {timeframe} "
                    f"{result.status} bars={result.bars} new={result.written} "
                    f"oldest={result.oldest or '-'} "
                    f"({elapsed:.0f}s, {stats.candles_written} Kerzen geschrieben)"
                    + (f" — {result.reason}" if result.reason else ""),
                    flush=True,
                )

    try:
        await asyncio.gather(
            *(process(asset_id, symbol, exchange) for asset_id, symbol, exchange in targets)
        )
    finally:
        for provider in providers.values():
            with contextlib.suppress(Exception):
                await provider.close()
        await dispose_engine()

    report = {
        "started_at": stats.started_at,
        "finished_at": utc_now().isoformat(),
        "duration_seconds": round(time.monotonic() - t0, 1),
        "timeframes": timeframes,
        "days": args.days,
        "interrupted": stop.is_set(),
        "stats": asdict(stats),
        "failures": [
            {"symbol": e.symbol, "timeframe": e.timeframe, "reason": e.reason}
            for e in state_store.entries()
            if e.status in {"failed", "partial"}
        ],
    }
    report_path = Path(args.report_file)
    await asyncio.to_thread(_write_report, report_path, report)

    print(json.dumps(report["stats"], indent=2), flush=True)
    print(f"Report: {report_path}", flush=True)
    logger.info("backfill_candles_done", **asdict(stats))
    return 1 if stop.is_set() else 0


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGINT/SIGTERM sauber behandeln, damit der State erhalten bleibt."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, stop.set)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backfill_candles",
        description="Tiefe Kerzenhistorie fuer das Universe nachladen (resumierbar).",
    )
    parser.add_argument(
        "-t", "--timeframe", default="1h", help="Timeframe oder Kommaliste, z. B. 1h,4h"
    )
    parser.add_argument("--days", type=int, default=190, help="Lookback in Tagen")
    parser.add_argument("--symbols", help="Kommaliste von Symbolen statt des ganzen Universe")
    parser.add_argument("--symbols-file", help="Datei mit einem Symbol pro Zeile")
    parser.add_argument("--exchange", help="Nur Assets dieser Boerse")
    parser.add_argument("--limit", type=int, help="Maximale Anzahl Assets")
    parser.add_argument(
        "--all-assets",
        action="store_true",
        help="Auch Assets ausserhalb des aktiven Universe (Vorsicht: Prune loescht diese)",
    )
    parser.add_argument("--concurrency", type=int, default=4, help="Parallele Symbole")
    parser.add_argument("--sleep-ms", type=int, default=0, help="Pause zwischen Fenstern")
    parser.add_argument(
        "--tolerance-bars",
        type=int,
        default=4,
        help="Toleranz in Bars, ab der vorhandene Historie als tief genug gilt",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Bereits abgedeckte Symbole ueberspringen (Default: an)",
    )
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--report-file", default=str(DEFAULT_REPORT_FILE))
    parser.add_argument("--dry-run", action="store_true", help="Nur laden, nichts schreiben")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
