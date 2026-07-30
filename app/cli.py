"""Kommandozeilen-Schnittstelle.

Aufruf: ``python -m app.cli <kommando>``.

Wichtigste Kommandos:
- ``worker``            startet Telegram-Bot und Scheduler
- ``backtest``          fuehrt einen Backtest aus
- ``analyze``           einmalige Analyse auf der Konsole
- ``scan``              einmaliger Marktscan
- ``universe refresh``  Top-N Market Cap laden und mappen
- ``data prune``        nur Top-N + Retention-Fenster behalten
- ``data backfill``     Historie fuer Top-N nachladen
- ``seed``              Grunddaten anlegen
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timedelta
from typing import Annotated

import typer

from app.backtesting.metrics import summarize_for_display
from app.bot.application import build_bot_application
from app.bot.formatting import format_analysis_message
from app.container import build_container
from app.core.config import Settings, get_settings
from app.core.errors import AlphaTradeOracleError
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.services.analysis_service import AnalysisOutcome
from app.services.scan_service import ScanService, SignalDispatcher

logger = get_logger(__name__)

cli = typer.Typer(
    add_completion=False,
    help="Alpha Trade Oracle Bot — Analyse, Backtesting und Betrieb. Keine Orderausfuehrung.",
)


@cli.command()
def worker() -> None:
    """Telegram-Bot und Scheduler starten (Long Polling)."""
    asyncio.run(_run_worker())


@cli.command()
def analyze(
    symbol: Annotated[str, typer.Argument(help="Handelspaar, z. B. BTCUSDT")],
    timeframes: Annotated[
        str | None, typer.Option("--timeframes", help="Kommaliste, z. B. 1h,4h,1d")
    ] = None,
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = False,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="LLM-Zusammenfassung ueberspringen")
    ] = False,
) -> None:
    """Einmalige Analyse ausfuehren und auf der Konsole ausgeben."""
    asyncio.run(_run_analyze(symbol, timeframes, persist, no_llm))


@cli.command()
def scan(
    symbols: Annotated[
        str | None, typer.Option("--symbols", help="Kommaliste; sonst Universe/Watchlist/Defaults")
    ] = None,
    dispatch: Annotated[
        bool, typer.Option("--dispatch/--no-dispatch", help="Signale per Telegram versenden")
    ] = False,
    universe: Annotated[
        bool,
        typer.Option(
            "--universe/--no-universe",
            help="Universe-Batch scannen (ignoriert Watchlist-Defaults)",
        ),
    ] = False,
) -> None:
    """Einmaligen Marktscan ausfuehren."""
    asyncio.run(_run_scan(symbols, dispatch, use_universe=universe or None))


universe_app = typer.Typer(help="Market-Cap-Universe verwalten.")
cli.add_typer(universe_app, name="universe")


@universe_app.command("refresh")
def universe_refresh() -> None:
    """Top-N Market Cap von CoinGecko laden und auf Boersen-Paare mappen."""
    asyncio.run(_run_universe_refresh())


data_app = typer.Typer(help="Marktdaten-Hygiene und Historie.")
cli.add_typer(data_app, name="data")


@data_app.command("prune")
def data_prune() -> None:
    """Assets ausserhalb Top-N deaktivieren und alte/fremde Kerzen loeschen."""
    asyncio.run(_run_data_prune())


@data_app.command("backfill")
def data_backfill(
    days: Annotated[
        int | None,
        typer.Option("--days", help="Historie in Tagen (Default: CANDLE_RETENTION_DAYS)"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Max. Assets (Default: alle Top-N)"),
    ] = None,
) -> None:
    """Kerzenhistorie fuer Top-N Assets nachladen (fuer Backtests)."""
    asyncio.run(_run_data_backfill(days, limit))


paper_app = typer.Typer(help="Paper-Trading verwalten.")
cli.add_typer(paper_app, name="paper")


@paper_app.command("backfill")
def paper_backfill(
    since: Annotated[
        str,
        typer.Option("--since", help="ISO-Datum/Zeit oder 'today' (UTC)"),
    ] = "today",
    dispatched_only: Annotated[
        bool,
        typer.Option("--dispatched-only/--all-qualifying", help="Nur versendete Signale"),
    ] = False,
    update_prices: Annotated[
        bool,
        typer.Option("--update/--no-update", help="Danach offene Positionen gegen Kurse pruefen"),
    ] = True,
) -> None:
    """Signale ab einem Zeitpunkt als Paper-Trades nachziehen."""
    asyncio.run(_run_paper_backfill(since, dispatched_only, update_prices))


@paper_app.command("rebuild")
def paper_rebuild(
    since: Annotated[
        str,
        typer.Option("--since", help="ISO-Datum/Zeit oder 'today' (UTC)"),
    ] = "today",
    dispatched_only: Annotated[
        bool,
        typer.Option("--dispatched-only/--all-qualifying", help="Nur versendete Signale"),
    ] = False,
    one_per_symbol: Annotated[
        bool,
        typer.Option(
            "--one-per-symbol/--all-signals",
            help="Nur juengstes Signal je Symbol (Default: alle chronologisch fuer HTF-Retro)",
        ),
    ] = False,
) -> None:
    """Paper-Ledger zuruecksetzen und mit aktuellen TP-Multiples / HTF-These neu berechnen."""
    asyncio.run(_run_paper_rebuild(since, dispatched_only, one_per_symbol))


@cli.command()
def backtest(
    symbol: Annotated[str, typer.Option("--symbol", help="Handelspaar, z. B. BTCUSDT")],
    timeframe: Annotated[str, typer.Option("--timeframe")] = "1h",
    start: Annotated[
        str | None, typer.Option("--start", help="ISO-Datum, z. B. 2024-01-01")
    ] = None,
    end: Annotated[str | None, typer.Option("--end", help="ISO-Datum, z. B. 2025-01-01")] = None,
    fee: Annotated[float, typer.Option("--fee", help="Gebuehr in Prozent je Seite")] = 0.1,
    slippage: Annotated[float, typer.Option("--slippage", help="Slippage in Prozent")] = 0.05,
    capital: Annotated[float, typer.Option("--capital", help="Startkapital")] = 10_000.0,
    persist: Annotated[bool, typer.Option("--persist/--no-persist")] = True,
) -> None:
    """Backtest ausfuehren und Kennzahlen ausgeben."""
    asyncio.run(_run_backtest(symbol, timeframe, start, end, fee, slippage, capital, persist))


@cli.command()
def seed() -> None:
    """Grunddaten anlegen: Standardsymbole, Strategie und Gewichtung."""
    from scripts.seed import run_seed

    asyncio.run(run_seed())


@cli.command()
def check() -> None:
    """Verbindungen pruefen: Datenbank, Redis, Marktdaten, LLM."""
    asyncio.run(_run_check())


# ---------------------------------------------------------------------------
# Implementierungen
# ---------------------------------------------------------------------------


def _build_telegram_dispatcher(settings: Settings) -> SignalDispatcher:
    """Dispatcher, der sich je Zustellung eine eigene DB-Session holt.

    Der ScanService darf keine langlebige Session halten, weil ein Scan mehrere
    Minuten dauern kann. Deshalb wird pro Zustellung eine kurze Transaktion
    geoeffnet.
    """
    from telegram import Bot

    from app.bot.notifier import TelegramNotifier, TelegramSignalDispatcher

    notifier = TelegramNotifier(Bot(settings.telegram_bot_token.get_secret_value()), settings)

    class SessionScopedDispatcher(SignalDispatcher):
        async def dispatch(
            self, outcome: AnalysisOutcome
        ) -> list[tuple[int, int | None, str | None]]:
            async with session_scope() as session:
                return await TelegramSignalDispatcher(notifier, session, settings).dispatch(outcome)

    return SessionScopedDispatcher()


async def _run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.app_env != "development")

    if not settings.telegram_configured:
        typer.secho(
            "TELEGRAM_BOT_TOKEN ist nicht gesetzt. Der Worker kann nicht starten.\n"
            "Token via @BotFather erzeugen und in die .env eintragen.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    container = build_container(settings)
    from app.scheduler.runner import SchedulerRunner

    # Der ScanService muss vor der Application existieren, weil die Handler ihn
    # brauchen. Der Notifier nutzt daher eine eigene Bot-Instanz statt
    # application.bot — das vermeidet eine zirkulaere Abhaengigkeit.
    scan_service = ScanService(
        container.analysis_service,
        container.deduplicator,
        dispatcher=_build_telegram_dispatcher(settings),
        paper_trading=container.paper_trading,
        settings=settings,
    )
    container.scan_service = scan_service

    application = build_bot_application(
        container.analysis_service,
        settings=settings,
        scan_service=scan_service,
        backtest_service=container.backtest_service if settings.enable_backtesting else None,
        paper_trading=container.paper_trading if settings.enable_paper_trading else None,
    )

    scheduler = SchedulerRunner(
        scan_service,
        settings,
        universe_service=container.universe_service,
        paper_trading=container.paper_trading,
        provider=container.provider,
        providers=container.universe_providers,
    )

    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        logger.info("worker_stop_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows unterstuetzt add_signal_handler nicht; KeyboardInterrupt greift dort.
            signal.signal(sig, _request_stop)

    await application.initialize()
    await application.start()
    if application.updater is None:
        raise RuntimeError("Telegram-Updater ist nicht verfuegbar.")
    await application.updater.start_polling(drop_pending_updates=True)
    await scheduler.start()

    bot_info = await application.bot.get_me()
    typer.secho(
        f"Worker laeuft. Bot: @{bot_info.username}. Beenden mit Strg+C.",
        fg=typer.colors.GREEN,
    )
    logger.info("worker_started", bot_username=bot_info.username)

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await scheduler.shutdown()
        if application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await container.aclose()
        logger.info("worker_stopped")


async def _run_analyze(symbol: str, timeframes: str | None, persist: bool, no_llm: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    tf_list = [t.strip() for t in timeframes.split(",")] if timeframes else None

    try:
        if persist:
            async with session_scope() as session:
                outcome = await container.analysis_service.analyze(
                    symbol,
                    timeframes=tf_list,
                    session=session,
                    persist=True,
                    use_llm=False if no_llm else None,
                )
        else:
            outcome = await container.analysis_service.analyze(
                symbol, timeframes=tf_list, persist=False, use_llm=False if no_llm else None
            )
    except AlphaTradeOracleError as exc:
        typer.secho(f"Analyse nicht moeglich: {exc}", fg=typer.colors.RED)
        await container.aclose()
        raise typer.Exit(code=1) from exc

    result = outcome.result
    typer.echo("")
    typer.secho(f"=== {result.symbol} ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Richtung:        {result.direction.value}")
    typer.echo(f"Score:           {result.score:.2f}/100")
    typer.echo(f"Konfidenz:       {result.confidence.value}")
    typer.echo(f"Marktphase:      {result.market_phase.value}")
    typer.echo(f"Referenzkurs:    {result.reference_price}")
    typer.echo(f"Datenqualitaet:  {result.data_quality:.1f}/100")
    typer.echo(f"Timeframes:      {', '.join(result.analyzed_timeframes)}")
    if result.no_trade_reason:
        typer.secho(f"Kein Trade:      {result.no_trade_reason}", fg=typer.colors.YELLOW)

    if result.risk is not None:
        risk = result.risk
        typer.echo("")
        typer.echo(f"Entry:           {risk.entry_low:.6g} - {risk.entry_high:.6g}")
        typer.echo(f"Stop-Loss:       {risk.stop_loss:.6g} ({risk.stop_distance_percent:.2f}%)")
        typer.echo(
            f"Take Profit:     {risk.take_profit_1:.6g} / "
            f"{risk.take_profit_2:.6g} / {risk.take_profit_3:.6g}"
        )
        typer.echo(f"Chance/Risiko:   {risk.risk_reward_ratio:.2f}")
        typer.echo(f"Ungueltig bei:   {risk.invalidation_note}")

    typer.echo("")
    typer.secho("Score-Aufschluesselung:", bold=True)
    for name, values in result.score_breakdown().items():
        typer.echo(
            f"  {name:<18} roh {values['raw_score']:>7} x {values['weight']:<6} "
            f"= {values['weighted_score']}"
        )

    typer.echo("")
    typer.secho("Bestaetigungen:", bold=True)
    for reason in result.reasons:
        typer.echo(f"  + {reason}")
    if result.counter_arguments:
        typer.secho("Gegenargumente:", bold=True)
        for counter in result.counter_arguments:
            typer.echo(f"  - {counter}")

    if outcome.llm_analysis is not None:
        typer.echo("")
        typer.secho("LLM-Einordnung:", bold=True)
        typer.echo(f"  {outcome.llm_analysis.summary}")
    elif outcome.llm_call is not None:
        typer.secho(
            f"\nLLM nicht verwendet (Status: {outcome.llm_call.status}). "
            "Die Ausgabe ist rein regelbasiert.",
            fg=typer.colors.YELLOW,
        )

    typer.echo("")
    typer.secho(
        "Hinweis: Keine Finanzberatung. Kryptowaehrungen sind hochriskant.",
        fg=typer.colors.YELLOW,
    )

    # Telegram-Vorschau, damit die Formatierung ohne Bot pruefbar ist.
    preview = format_analysis_message(
        result,
        price_precision=outcome.price_precision,
        display_timezone=settings.display_timezone,
        llm_analysis=outcome.llm_analysis,
    )
    typer.echo("")
    typer.secho("--- Telegram-Vorschau (MarkdownV2, roh) ---", fg=typer.colors.BRIGHT_BLACK)
    typer.echo(preview)

    await container.aclose()


async def _run_scan(
    symbols: str | None, dispatch: bool, *, use_universe: bool | None = None
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    dispatcher = None
    if dispatch:
        if not settings.telegram_configured:
            typer.secho(
                "Versand angefordert, aber TELEGRAM_BOT_TOKEN fehlt. Es wird nur analysiert.",
                fg=typer.colors.YELLOW,
            )
        else:
            dispatcher = _build_telegram_dispatcher(settings)

    scan_service = ScanService(
        container.analysis_service,
        container.deduplicator,
        dispatcher=dispatcher,
        paper_trading=container.paper_trading,
        settings=settings,
    )

    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None

    async with session_scope() as session:
        result = await scan_service.scan(
            session,
            symbols=symbol_list,
            dispatch=dispatcher is not None,
            use_universe=use_universe,
        )

    typer.echo("")
    typer.secho("Scan abgeschlossen", fg=typer.colors.GREEN, bold=True)
    for key, value in result.as_summary().items():
        typer.echo(f"  {key}: {value}")
    for symbol, detail in result.suppression_details:
        typer.echo(f"  unterdrueckt {symbol}: {detail}")
    for symbol, error in result.failures:
        typer.secho(f"  Fehler {symbol}: {error}", fg=typer.colors.RED)

    await container.aclose()


async def _run_universe_refresh() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    try:
        async with session_scope() as session:
            result = await container.universe_service.refresh(session)
    except AlphaTradeOracleError as exc:
        typer.secho(f"Universe-Refresh fehlgeschlagen: {exc}", fg=typer.colors.RED)
        await container.aclose()
        raise typer.Exit(code=1) from exc

    typer.echo("")
    typer.secho("Universe-Refresh abgeschlossen", fg=typer.colors.GREEN, bold=True)
    for key, value in result.as_summary().items():
        typer.echo(f"  {key}: {value}")
    if result.symbols:
        preview = ", ".join(result.symbols[:15])
        suffix = " ..." if len(result.symbols) > 15 else ""
        typer.echo(f"  examples: {preview}{suffix}")

    await container.aclose()


async def _run_data_prune() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)
    async with session_scope() as session:
        result = await container.data_retention.prune(session)
    typer.echo("")
    typer.secho("Data-Prune abgeschlossen", fg=typer.colors.GREEN, bold=True)
    for key, value in result.as_summary().items():
        typer.echo(f"  {key}: {value}")
    await container.aclose()


async def _run_data_backfill(days: int | None, limit: int | None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)
    async with session_scope() as session:
        result = await container.data_retention.backfill_history(
            session, days=days, limit_assets=limit
        )
    typer.echo("")
    typer.secho("History-Backfill abgeschlossen", fg=typer.colors.GREEN, bold=True)
    for key, value in result.as_summary().items():
        typer.echo(f"  {key}: {value}")
    if result.failures:
        typer.secho(f"  erste Fehler: {result.failures[:5]}", fg=typer.colors.YELLOW)
    await container.aclose()


async def _run_paper_backfill(
    since: str,
    dispatched_only: bool,
    update_prices: bool,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    if since.strip().lower() == "today":
        now = utc_now()
        since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since_dt = ensure_utc(datetime.fromisoformat(since))

    async with session_scope() as session:
        result = await container.paper_trading.backfill_from_signals(
            session,
            since=since_dt,
            dispatched_only=dispatched_only,
            one_per_symbol=True,
        )
        updated = 0
        if update_prices and result.opened > 0:
            account = await container.paper_trading.get_or_create_account(session)
            from app.repositories.paper_repository import PaperRepository
            from app.scheduler.jobs import _collect_prices

            opens = await PaperRepository(session).list_open_positions(account.id)
            symbols = [position.symbol for position in opens]
            if symbols:
                prices = await _collect_prices(
                    container.provider,
                    symbols,
                    providers=container.universe_providers,
                )
                changed = await container.paper_trading.update_open_positions(session, prices)
                updated = len(changed)
        summary = await container.paper_trading.summary(session)

    typer.echo("")
    typer.secho("Paper-Backfill abgeschlossen", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  since:              {since_dt.isoformat()}")
    typer.echo(f"  considered:         {result.considered}")
    typer.echo(f"  opened:             {result.opened}")
    typer.echo(f"  skipped_existing:   {result.skipped_existing}")
    typer.echo(f"  skipped_filters:    {result.skipped_filters}")
    typer.echo(f"  skipped_cash:       {result.skipped_cash}")
    typer.echo(f"  price_updates:      {updated}")
    if result.opened_symbols:
        typer.echo(f"  symbols:            {', '.join(result.opened_symbols)}")
    typer.echo(
        f"  equity:             ${summary.equity:,.2f}  "
        f"(cash ${summary.cash_balance:,.2f}, open {summary.open_positions})"
    )

    await container.aclose()


async def _run_paper_rebuild(since: str, dispatched_only: bool, one_per_symbol: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    if since.strip().lower() == "today":
        now = utc_now()
        since_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since_dt = ensure_utc(datetime.fromisoformat(since))

    async with session_scope() as session:
        result = await container.paper_trading.rebuild_from_signals(
            session,
            since=since_dt,
            provider=container.provider,
            providers=container.universe_providers,
            dispatched_only=dispatched_only,
            one_per_symbol=one_per_symbol,
        )
        summary = await container.paper_trading.summary(session)
        opened = result.backfill.opened if result.backfill else 0
        symbols = result.backfill.opened_symbols if result.backfill else []

    typer.echo("")
    typer.secho("Paper-Rebuild abgeschlossen", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  since:              {since_dt.isoformat()}")
    typer.echo(f"  reset_positions:    {result.reset_positions}")
    typer.echo(f"  opened:             {opened}")
    typer.echo(f"  htf_filled:         {result.htf_filled}")
    typer.echo(f"  htf_skipped:        {result.htf_skipped}")
    typer.echo(f"  htf_still_pending:  {result.htf_still_pending}")
    typer.echo(f"  replayed:           {result.replayed}")
    typer.echo(f"  still_open:         {result.still_open}")
    if symbols:
        typer.echo(f"  symbols:            {', '.join(symbols[:40])}{'...' if len(symbols) > 40 else ''}")
    typer.echo(
        f"  equity:             ${summary.equity:,.2f}  "
        f"(cash ${summary.cash_balance:,.2f}, realized ${summary.realized_pnl:,.2f})"
    )
    typer.echo(
        f"  closed:             {summary.closed_trades}  "
        f"win_rate {summary.win_rate * 100:.0f}%  PF {summary.profit_factor:.2f}  "
        f"pending {summary.pending_positions}"
    )

    await container.aclose()


async def _run_backtest(
    symbol: str,
    timeframe: str,
    start: str | None,
    end: str | None,
    fee: float,
    slippage: float,
    capital: float,
    persist: bool,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    end_dt = ensure_utc(datetime.fromisoformat(end)) if end else utc_now()
    start_dt = ensure_utc(datetime.fromisoformat(start)) if start else end_dt - timedelta(days=365)

    container = build_container(settings)

    try:
        if persist:
            async with session_scope() as session:
                report = await container.backtest_service.run(
                    symbol,
                    timeframe,
                    start_dt,
                    end_dt,
                    session=session,
                    fee_percent=fee,
                    slippage_percent=slippage,
                    initial_capital=capital,
                )
        else:
            report = await container.backtest_service.run(
                symbol,
                timeframe,
                start_dt,
                end_dt,
                fee_percent=fee,
                slippage_percent=slippage,
                initial_capital=capital,
                persist=False,
            )
    except AlphaTradeOracleError as exc:
        typer.secho(f"Backtest fehlgeschlagen: {exc}", fg=typer.colors.RED)
        await container.aclose()
        raise typer.Exit(code=1) from exc

    typer.echo("")
    typer.secho(f"=== Backtest {symbol.upper()} {timeframe} ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Zeitraum:        {start_dt.date()} bis {end_dt.date()}")
    typer.echo(f"Kerzen geladen:  {report.candles_loaded}")
    typer.echo(f"Signale:         {report.outcome.signals_generated}")
    if report.run_id is not None:
        typer.echo(f"Lauf-ID:         {report.run_id}")
    typer.echo("")

    for scope in ("overall", "long", "short"):
        metrics = report.metrics.get(scope)
        if not metrics or metrics.get("trade_count", 0) == 0:
            continue
        label = {"overall": "Gesamt", "long": "Long", "short": "Short"}[scope]
        for line in summarize_for_display(metrics, label):
            typer.echo(line)
        typer.echo("")

    typer.secho(
        "Hinweis: Historische Simulationsergebnisse sind keine Zusage fuer zukuenftige "
        "Ergebnisse. Es wurden keine echten Orders ausgefuehrt.",
        fg=typer.colors.YELLOW,
    )

    await container.aclose()


async def _run_check() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    report = await container.health_service.readiness()

    typer.echo("")
    typer.secho("Verbindungspruefung", fg=typer.colors.CYAN, bold=True)
    for component in report.components:
        marker = "OK  " if component.healthy else "FEHL"
        colour = typer.colors.GREEN if component.healthy else typer.colors.RED
        requirement = "erforderlich" if component.required else "optional"
        typer.secho(
            f"  [{marker}] {component.name:<14} ({requirement}) {component.detail}",
            fg=colour,
        )

    typer.echo("")
    if report.is_ready:
        typer.secho("System ist bereit.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("System ist NICHT bereit.", fg=typer.colors.RED, bold=True)

    await container.aclose()
    if not report.is_ready:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    cli()
