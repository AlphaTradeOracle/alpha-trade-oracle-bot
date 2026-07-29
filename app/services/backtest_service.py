"""BacktestService — laedt historische Daten, simuliert und persistiert Ergebnisse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import WARMUP_CANDLES, BacktestConfig, BacktestEngine, BacktestOutcome
from app.backtesting.metrics import compute_metrics
from app.core.config import Settings, get_settings
from app.core.enums import BacktestStatus
from app.core.errors import BacktestError
from app.core.logging import get_logger
from app.core.time import ensure_utc, timeframe_to_timedelta
from app.market_data.base import MarketDataProvider
from app.repositories.backtest_repository import BacktestRepository
from app.repositories.strategy_repository import StrategyRepository
from app.strategies.weights import DEFAULT_WEIGHTS

logger = get_logger(__name__)


@dataclass
class BacktestReport:
    """Ergebnis eines Backtests inkl. Kennzahlen und Datenbank-ID."""

    run_id: int | None
    outcome: BacktestOutcome
    metrics: dict[str, dict[str, float]]
    candles_loaded: int


class BacktestService:
    """Fuehrt reproduzierbare Backtests aus."""

    def __init__(self, provider: MarketDataProvider, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider = provider

    async def run(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        session: AsyncSession | None = None,
        fee_percent: float = 0.1,
        slippage_percent: float = 0.05,
        initial_capital: float | None = None,
        persist: bool = True,
        prefer_db: bool = False,
        **config_overrides: object,
    ) -> BacktestReport:
        """Backtest ausfuehren.

        Der Ladezeitraum beginnt bewusst vor ``start``, damit die Indikatoren
        aufgewaermt sind. Ohne diesen Vorlauf waeren die ersten Signale des
        Zeitraums nicht berechenbar.

        ``prefer_db=True`` laedt Kerzen aus ``market_candles`` (Session noetig)
        statt vom Exchange-API.
        """
        normalized = symbol.upper().strip()
        start_utc = ensure_utc(start)
        end_utc = ensure_utc(end)

        if end_utc <= start_utc:
            raise BacktestError(
                "Das Enddatum muss nach dem Startdatum liegen.",
                detail=f"start={start_utc.isoformat()} end={end_utc.isoformat()}",
            )
        if prefer_db and session is None:
            raise BacktestError(
                "DB-Backtest benoetigt eine offene Datenbank-Session.",
                detail="prefer_db=True ohne session",
            )

        weights = DEFAULT_WEIGHTS
        strategy_version_id: int | None = None
        if session is not None:
            loaded_weights, strategy_version_id = await StrategyRepository(session).load_weights()
            if loaded_weights is not None:
                weights = loaded_weights

        config = BacktestConfig.from_settings(
            self._settings,
            symbol=normalized,
            timeframe=timeframe,
            weights=weights,
            fee_percent=fee_percent,
            slippage_percent=slippage_percent,
            initial_capital=(
                initial_capital if initial_capital is not None else self._settings.reference_capital
            ),
            **config_overrides,
        )

        timeframes = list(config.timeframes) if config.use_multi_timeframe else [timeframe]
        mtf_frames: dict[str, object] = {}
        candles_loaded = 0

        for tf in timeframes:
            warmup_start = start_utc - timeframe_to_timedelta(tf) * WARMUP_CANDLES
            if prefer_db:
                from app.repositories.asset_repository import AssetRepository

                series = await AssetRepository(session).load_candle_series(
                    normalized,
                    tf,
                    start_time=warmup_start,
                    end_time=end_utc,
                    limit=100_000,
                )
            else:
                series = await self._provider.get_candles(
                    normalized, tf, limit=100_000, start_time=warmup_start, end_time=end_utc
                )
            if series.is_empty:
                if tf == timeframe:
                    raise BacktestError(
                        f"Fuer {normalized} {tf} wurden im Zeitraum keine Kerzen gefunden.",
                        detail=f"{start_utc.date()} bis {end_utc.date()} source={'db' if prefer_db else 'api'}",
                    )
                logger.warning(
                    "backtest_timeframe_skipped",
                    symbol=normalized,
                    timeframe=tf,
                    reason="no_candles",
                    source="db" if prefer_db else "api",
                )
                continue
            # Sekundaere TFs mit zu wenig Historie ueberspringen (MTF-Warmup),
            # sonst scheitert der gesamte Lauf an z. B. kurzem 1d.
            min_bars = WARMUP_CANDLES + 10
            if tf != timeframe and len(series) < min_bars:
                logger.warning(
                    "backtest_timeframe_skipped",
                    symbol=normalized,
                    timeframe=tf,
                    reason="insufficient_warmup",
                    candles=len(series),
                    required=min_bars,
                    source="db" if prefer_db else "api",
                )
                continue
            mtf_frames[tf] = series.to_dataframe()
            candles_loaded += len(series)

        if timeframe not in mtf_frames:
            raise BacktestError(
                f"Fuer {normalized} {timeframe} wurden im Zeitraum keine Kerzen gefunden.",
                detail=f"{start_utc.date()} bis {end_utc.date()} source={'db' if prefer_db else 'api'}",
            )

        repository = BacktestRepository(session) if session is not None and persist else None
        run_id: int | None = None

        if repository is not None:
            run = await repository.create_run(
                symbol=normalized,
                timeframe=timeframe,
                start_at=start_utc,
                end_at=end_utc,
                initial_capital=config.initial_capital,
                fee_percent=fee_percent,
                slippage_percent=slippage_percent,
                strategy_version_id=strategy_version_id,
                parameters={
                    "min_score": config.min_score,
                    "min_risk_reward_ratio": config.min_risk_reward_ratio,
                    "atr_multiplier": config.atr_multiplier,
                    "warmup_candles": WARMUP_CANDLES,
                    "use_multi_timeframe": config.use_multi_timeframe,
                    "timeframes": list(config.timeframes),
                    "cooldown_minutes": config.cooldown_minutes,
                    "require_strong_signals": config.require_strong_signals,
                    "scale_out_enabled": config.scale_out_enabled,
                    "move_stop_to_breakeven_after_tp1": config.move_stop_to_breakeven_after_tp1,
                    "weights": weights.model_dump(),
                },
            )
            run_id = run.id

        try:
            engine = BacktestEngine(config)
            if config.use_multi_timeframe and len(mtf_frames) > 1:
                outcome = engine.run(mtf_frames=mtf_frames)  # type: ignore[arg-type]
            else:
                outcome = engine.run(mtf_frames[timeframe])  # type: ignore[arg-type]
            metrics = compute_metrics(outcome)
        except Exception as exc:
            if repository is not None and run_id is not None:
                await repository.finish_run(
                    run_id, status=BacktestStatus.FAILED, error_message=str(exc)
                )
            raise

        if repository is not None and run_id is not None:
            await repository.add_trades(
                run_id, [trade.to_db_row() for trade in outcome.trades if trade.is_closed]
            )
            await repository.add_metrics(run_id, metrics)
            await repository.finish_run(run_id, status=BacktestStatus.COMPLETED)

        overall = metrics.get("overall", {})
        logger.info(
            "backtest_report_ready",
            symbol=normalized,
            timeframe=timeframe,
            run_id=run_id,
            trades=int(overall.get("trade_count", 0)),
            win_rate=overall.get("win_rate"),
            net_profit=overall.get("net_profit"),
        )

        return BacktestReport(
            run_id=run_id, outcome=outcome, metrics=metrics, candles_loaded=candles_loaded
        )
