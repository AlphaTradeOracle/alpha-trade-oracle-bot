"""Backtesting-Engine.

**Look-ahead-Freiheit ist die zentrale Eigenschaft dieses Moduls.** Sie wird auf
drei Ebenen sichergestellt:

1. Die Signalerzeugung an Index ``i`` sieht ausschliesslich ``df.iloc[:i + 1]``,
   also nur abgeschlossene Kerzen bis einschliesslich der aktuellen.
2. Der Einstieg erfolgt frueheastens auf der **Eroeffnung der Kerze ``i + 1``**.
   Ein Signal aus dem Schlusskurs von ``i`` kann nicht in derselben Kerze
   ausgefuehrt werden.
3. Die Ausstiegspruefung beginnt ebenfalls bei ``i + 1``.

Die verwendete Indicator Engine und Signal-Engine sind dieselben Objekte wie im
Live-Betrieb — es gibt keine Backtest-Variante der Fachlogik.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from app.core.enums import ExitReason, SignalDirection
from app.core.errors import BacktestError
from app.core.logging import get_logger
from app.core.time import ensure_utc, timeframe_minutes, timeframe_to_timedelta
from app.indicators.engine import IndicatorEngine
from app.market_data.types import Candle
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.market_regime import MarketRegimeEngine, bias_to_market_regime
from app.signals.regime import regime_from_indicators
from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry, levels_from_entry_sl
from app.signals.risk import RiskConfig, RiskManager
from app.signals.types import SignalResult
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

#: Kerzen, die vor dem ersten Signal fuer die Indikator-Aufwaermphase noetig sind.
WARMUP_CANDLES = 210

#: Anteil der Position bei TP1 / TP2 / TP3 (Summe 1.0).
DEFAULT_SCALE_OUT_FRACTIONS = (0.5, 0.25, 0.25)
LEGACY_SCALE_OUT_FRACTIONS = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)

@dataclass(frozen=True)
class BacktestConfig:
    """Parameter eines Backtests."""

    symbol: str
    timeframe: str
    fee_percent: float = 0.1
    #: 0 = Paper-Paritaet (nur Fee). Explizit setzen fuer Stress-Tests.
    slippage_percent: float = 0.0
    initial_capital: float = 10_000.0
    min_score: float = 75.0
    #: Optional Long-only floor (defaults to ``min_score``). Lets us raise the
    #: long gate without tightening the short mirror ``(100-score) < min_score``.
    long_min_score: float | None = None
    #: Optional: reject shorts with score above this (live SIGNAL_SHORT_MAX_SCORE).
    short_max_score: float | None = None
    #: Optional: reject shorts with score at/below this (live SIGNAL_SHORT_MIN_SCORE).
    short_min_score: float | None = None
    min_risk_reward_ratio: float = 2.0
    atr_multiplier: float = 1.8
    max_atr_percent: float = 12.0
    expiry_multiplier: int = 4
    timeframes: tuple[str, ...] = ("15m", "1h", "4h", "1d")
    use_multi_timeframe: bool = False
    cooldown_minutes: int = 120
    require_strong_signals: bool = False
    block_range_market: bool = True
    min_adx: float = 20.0
    rsi_long_max: float = 75.0
    rsi_short_min: float = 25.0
    regime_filter_enabled: bool = True
    #: Nur ein Trade gleichzeitig — ohne Positionsverwaltung waere die
    #: Kapitalkurve nicht interpretierbar.
    allow_concurrent_trades: bool = False
    #: Teilverkaeufe an TP1/TP2/TP3 statt All-or-nothing.
    scale_out_enabled: bool = True
    scale_out_fractions: tuple[float, float, float] = DEFAULT_SCALE_OUT_FRACTIONS
    #: Nach TP1 Stop auf Entry (Break-even) ziehen.
    move_stop_to_breakeven_after_tp1: bool = True
    #: Take-Profit als Vielfache von R (Stop-Abstand).
    tp_multipliers: tuple[float, float, float] = (1.5, 2.5, 4.0)
    #: Retest/Pullback-Entry statt naechster Primary-Open (IST).
    retest_entry_enabled: bool = True
    retest_zone_near: float = 0.55
    retest_zone_far: float = 1.0
    retest_pending_multiplier: int = 6
    retest_min_bars_in_zone: int = 1
    retest_trendline_gate: bool = True
    retest_trendline_buffer_atr: float = 0.1
    retest_trendline_lookback: int = 40
    retest_trendline_min_points: int = 2
    retest_trendline_min_r2: float = 0.85
    retest_trendline_min_clearance_atr: float = 0.0
    #: Nach TP1 Hold-Fenster verlaengern (wie Paper).
    expiry_multiplier_after_tp1: int = 48
    weights: StrategyWeights = DEFAULT_WEIGHTS

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        symbol: str,
        timeframe: str,
        weights: StrategyWeights = DEFAULT_WEIGHTS,
        **overrides: object,
    ) -> BacktestConfig:
        """Backtest-Konfiguration aus den zentralen Settings ableiten."""
        params: dict[str, object] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "fee_percent": settings.paper_fee_percent,
            "slippage_percent": 0.0,
            "min_score": settings.signal_min_score,
            "min_risk_reward_ratio": settings.min_risk_reward_ratio,
            "atr_multiplier": settings.atr_multiplier,
            "max_atr_percent": settings.max_atr_percent,
            "expiry_multiplier": settings.signal_expiry_multiplier,
            "timeframes": tuple(settings.timeframes),
            "use_multi_timeframe": True,
            "cooldown_minutes": settings.signal_cooldown_minutes,
            "require_strong_signals": settings.signal_require_strong,
            "block_range_market": settings.signal_block_range_market,
            "min_adx": settings.signal_min_adx,
            "rsi_long_max": settings.signal_rsi_long_max,
            "rsi_short_min": settings.signal_rsi_short_min,
            "regime_filter_enabled": settings.regime_filter_enabled,
            "scale_out_fractions": tuple(settings.parsed_scale_out_fractions),
            "move_stop_to_breakeven_after_tp1": settings.paper_move_stop_to_breakeven,
            "tp_multipliers": tuple(settings.parsed_tp_multipliers),
            "retest_entry_enabled": settings.backtest_retest_entry_enabled,
            "retest_zone_near": settings.paper_retest_zone_near,
            "retest_zone_far": settings.paper_retest_zone_far,
            "retest_pending_multiplier": settings.paper_retest_pending_multiplier,
            "retest_min_bars_in_zone": settings.paper_retest_min_bars_in_zone,
            "retest_trendline_gate": settings.signal_trendline_gate_enabled,
            "retest_trendline_buffer_atr": settings.signal_trendline_buffer_atr,
            "retest_trendline_lookback": settings.signal_trendline_lookback,
            "retest_trendline_min_points": settings.signal_trendline_min_points,
            "retest_trendline_min_r2": settings.signal_trendline_min_r2,
            "retest_trendline_min_clearance_atr": settings.signal_trendline_min_clearance_atr,
            "expiry_multiplier_after_tp1": settings.paper_expiry_multiplier_after_tp1,
            "short_max_score": settings.signal_short_max_score,
            "short_min_score": settings.signal_short_min_score,
            "weights": weights,
        }
        params.update(overrides)
        return cls(**params)  # type: ignore[arg-type]


@dataclass
class SimulatedTrade:
    """Ein simulierter Trade. Es wurde nie eine echte Order platziert."""

    symbol: str
    timeframe: str
    direction: SignalDirection
    entry_at: datetime
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    quantity: float
    risk_reward_planned: float
    signal_score: float
    expires_at: datetime
    remaining_quantity: float = 0.0
    current_stop: float = 0.0
    tp1_filled: bool = False
    tp2_filled: bool = False
    tp3_filled: bool = False
    exit_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    pnl_percent: float = 0.0
    holding_minutes: int = 0

    def __post_init__(self) -> None:
        if self.remaining_quantity <= 0:
            self.remaining_quantity = self.quantity
        if self.current_stop == 0.0:
            self.current_stop = self.stop_loss

    @property
    def is_closed(self) -> bool:
        return self.remaining_quantity <= 1e-12

    def to_db_row(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "entry_at": self.entry_at,
            "entry_price": Decimal(str(round(self.entry_price, 8))),
            "exit_at": self.exit_at,
            "exit_price": (
                Decimal(str(round(self.exit_price, 8))) if self.exit_price is not None else None
            ),
            "exit_reason": self.exit_reason.value if self.exit_reason else None,
            "stop_loss": Decimal(str(round(self.stop_loss, 8))),
            "take_profit_1": Decimal(str(round(self.take_profit_1, 8))),
            "take_profit_2": Decimal(str(round(self.take_profit_2, 8))),
            "take_profit_3": Decimal(str(round(self.take_profit_3, 8))),
            "quantity": Decimal(str(round(self.quantity, 8))),
            "gross_pnl": Decimal(str(round(self.gross_pnl, 8))),
            "fees": Decimal(str(round(self.fees, 8))),
            "net_pnl": Decimal(str(round(self.net_pnl, 8))),
            "pnl_percent": round(self.pnl_percent, 4),
            "risk_reward_planned": round(self.risk_reward_planned, 4),
            "holding_minutes": self.holding_minutes,
            "signal_score": round(self.signal_score, 2),
        }


@dataclass
class BacktestOutcome:
    """Ergebnis eines Backtests."""

    config: BacktestConfig
    trades: list[SimulatedTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    candles_evaluated: int = 0
    signals_generated: int = 0
    signals_skipped_below_score: int = 0
    signals_skipped_no_trade: int = 0
    signals_skipped_not_strong: int = 0
    signals_skipped_cooldown: int = 0


class BacktestEngine:
    """Simuliert die Live-Logik auf historischen Daten."""

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._indicators = IndicatorEngine(min_candles=WARMUP_CANDLES)
        self._regime_engine = MarketRegimeEngine()
        self._btc_mtf_frames: dict[str, pd.DataFrame] | None = None
        engine_config = SignalEngineConfig(
            weights=config.weights,
            primary_timeframe=config.timeframe,
            confirmation_timeframe="4h",
            min_risk_reward_ratio=config.min_risk_reward_ratio,
            max_atr_percent=config.max_atr_percent,
            expiry_multiplier=config.expiry_multiplier,
            enable_sentiment=False,
            block_range_market=config.block_range_market,
            min_adx=config.min_adx,
            rsi_long_max=config.rsi_long_max,
            rsi_short_min=config.rsi_short_min,
            regime_filter_enabled=config.regime_filter_enabled,
            strategy_version_label="backtest:1",
        )
        self._signal_engine = SignalEngine(
            engine_config,
            RiskManager(
                RiskConfig(
                    atr_multiplier=config.atr_multiplier,
                    min_risk_reward_ratio=config.min_risk_reward_ratio,
                    reference_capital=config.initial_capital,
                    tp_multipliers=config.tp_multipliers,
                )
            ),
        )

    def run(
        self,
        df: pd.DataFrame | None = None,
        *,
        mtf_frames: dict[str, pd.DataFrame] | None = None,
        btc_mtf_frames: dict[str, pd.DataFrame] | None = None,
    ) -> BacktestOutcome:
        """Backtest auf historischen OHLCV-Daten ausfuehren."""
        self._btc_mtf_frames = btc_mtf_frames
        if mtf_frames is not None:
            return self._run_mtf(mtf_frames)
        if df is None:
            raise BacktestError(
                "Es wurden weder ein DataFrame noch Multi-Timeframe-Daten uebergeben.",
                detail="run(df=...) oder run(mtf_frames={...}) verwenden",
            )
        return self._run_single(df)

    def _market_regime_at(self, cutoff: datetime):
        """BTC-aligned regime at bar time; falls back to None when no BTC series."""
        if not self._config.regime_filter_enabled or not self._btc_mtf_frames:
            return None
        sliced: dict[str, pd.DataFrame] = {}
        for tf, frame in self._btc_mtf_frames.items():
            window = frame.loc[frame.index <= cutoff]
            if len(window) >= 50:
                sliced[tf] = window
        if not sliced:
            return None
        return self._regime_engine.resolve_from_btc_frames(sliced)

    def _run_single(self, df: pd.DataFrame) -> BacktestOutcome:
        """Backtest auf einem OHLCV-DataFrame ausfuehren.

        Args:
            df: Aufsteigend sortierter DataFrame mit UTC-DatetimeIndex und den
                Spalten ``open``, ``high``, ``low``, ``close``, ``volume``.
        """
        self._validate(df)

        outcome = BacktestOutcome(config=self._config)
        equity = self._config.initial_capital
        outcome.equity_curve.append(equity)

        open_trade: SimulatedTrade | None = None
        last_entry_at: datetime | None = None
        interval_minutes = timeframe_minutes(self._config.timeframe)
        total = len(df)

        for i in range(WARMUP_CANDLES, total - 2):
            outcome.candles_evaluated += 1

            if open_trade is not None:
                realized = self._process_open_trade(open_trade, df, i, interval_minutes)
                if realized:
                    equity += realized
                    outcome.equity_curve.append(equity)
                if open_trade.is_closed:
                    open_trade = None

            if open_trade is not None and not self._config.allow_concurrent_trades:
                continue

            window = df.iloc[: i + 1]
            signal = self._generate_signal(window, i)
            if not self._should_take_signal(signal, window, i, last_entry_at, outcome):
                continue

            outcome.signals_generated += 1
            if self._config.retest_entry_enabled:
                trade = self._open_trade_retest(signal, df, i, equity)  # type: ignore[arg-type]
            else:
                trade = self._open_trade(signal, df, i, equity)  # type: ignore[arg-type]
            if trade is not None:
                open_trade = trade
                last_entry_at = trade.entry_at
                outcome.trades.append(trade)

        if open_trade is not None and not open_trade.is_closed:
            realized = self._close_remaining(
                open_trade,
                exit_at=ensure_utc(_index_time(df, total - 1)),
                exit_price=float(df["close"].iloc[-1]),
                reason=ExitReason.END_OF_DATA,
                interval_minutes=interval_minutes,
            )
            equity += realized
            outcome.equity_curve.append(equity)

        self._log_completion(outcome)
        return outcome

    def _run_mtf(self, mtf_frames: dict[str, pd.DataFrame]) -> BacktestOutcome:
        """Multi-Timeframe-Backtest — identische Logik wie im Live-Scan."""
        primary_tf = self._config.timeframe
        if primary_tf not in mtf_frames:
            raise BacktestError(
                f"Primaerer Timeframe {primary_tf!r} fehlt in den geladenen Daten.",
                detail=f"Vorhanden: {', '.join(sorted(mtf_frames))}",
            )

        for timeframe, frame in mtf_frames.items():
            self._validate(frame, label=timeframe)

        primary_df = mtf_frames[primary_tf]
        outcome = BacktestOutcome(config=self._config)
        equity = self._config.initial_capital
        outcome.equity_curve.append(equity)

        open_trade: SimulatedTrade | None = None
        last_entry_at: datetime | None = None
        interval_minutes = timeframe_minutes(primary_tf)
        total = len(primary_df)

        for i in range(WARMUP_CANDLES, total - 2):
            outcome.candles_evaluated += 1

            if open_trade is not None:
                realized = self._process_open_trade(
                    open_trade, primary_df, i, interval_minutes
                )
                if realized:
                    equity += realized
                    outcome.equity_curve.append(equity)
                if open_trade.is_closed:
                    open_trade = None

            if open_trade is not None and not self._config.allow_concurrent_trades:
                continue

            cutoff = ensure_utc(_index_time(primary_df, i))
            signal = self._generate_signal_mtf(mtf_frames, cutoff, i)
            if not self._should_take_signal(signal, primary_df, i, last_entry_at, outcome):
                continue

            outcome.signals_generated += 1
            if self._config.retest_entry_enabled:
                trade = self._open_trade_retest(
                    signal, primary_df, i, equity  # type: ignore[arg-type]
                )
            else:
                trade = self._open_trade(signal, primary_df, i, equity)  # type: ignore[arg-type]
            if trade is not None:
                open_trade = trade
                last_entry_at = trade.entry_at
                outcome.trades.append(trade)

        if open_trade is not None and not open_trade.is_closed:
            realized = self._close_remaining(
                open_trade,
                exit_at=ensure_utc(_index_time(primary_df, total - 1)),
                exit_price=float(primary_df["close"].iloc[-1]),
                reason=ExitReason.END_OF_DATA,
                interval_minutes=interval_minutes,
            )
            equity += realized
            outcome.equity_curve.append(equity)

        self._log_completion(outcome)
        return outcome

    def _should_take_signal(
        self,
        signal: SignalResult | None,
        df: pd.DataFrame,
        index: int,
        last_entry_at: datetime | None,
        outcome: BacktestOutcome,
    ) -> bool:
        if signal is None:
            return False

        if signal.direction is SignalDirection.NO_TRADE or signal.no_trade_reason:
            outcome.signals_skipped_no_trade += 1
            return False

        if not signal.direction.is_actionable:
            outcome.signals_skipped_no_trade += 1
            return False

        if self._config.require_strong_signals and signal.direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }:
            outcome.signals_skipped_not_strong += 1
            return False

        long_floor = (
            self._config.long_min_score
            if self._config.long_min_score is not None
            else self._config.min_score
        )
        if signal.direction.is_long and signal.score < long_floor:
            outcome.signals_skipped_below_score += 1
            return False
        # Align with live dedup/paper gates: short_max / short_min only (no
        # mirror of min_score that would hard-cap shorts at 100-min_score).
        if (
            signal.direction.is_short
            and self._config.short_max_score is not None
            and signal.score > self._config.short_max_score
        ):
            outcome.signals_skipped_below_score += 1
            return False
        if (
            signal.direction.is_short
            and self._config.short_min_score is not None
            and signal.score <= self._config.short_min_score
        ):
            outcome.signals_skipped_below_score += 1
            return False

        if signal.risk is None:
            return False

        if self._config.cooldown_minutes > 0 and last_entry_at is not None:
            candle_time = ensure_utc(_index_time(df, index))
            elapsed = (candle_time - last_entry_at).total_seconds() / 60.0
            if elapsed < self._config.cooldown_minutes:
                outcome.signals_skipped_cooldown += 1
                return False

        return True

    def _log_completion(self, outcome: BacktestOutcome) -> None:
        logger.info(
            "backtest_completed",
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            candles=outcome.candles_evaluated,
            signals=outcome.signals_generated,
            trades=len(outcome.trades),
            skipped_no_trade=outcome.signals_skipped_no_trade,
            skipped_not_strong=outcome.signals_skipped_not_strong,
            skipped_cooldown=outcome.signals_skipped_cooldown,
        )

    # --- Teilschritte -----------------------------------------------------

    def _validate(self, df: pd.DataFrame, *, label: str | None = None) -> None:
        prefix = f"{label}: " if label else ""
        required = ("open", "high", "low", "close", "volume")
        missing = [column for column in required if column not in df.columns]
        if missing:
            raise BacktestError(
                f"{prefix}OHLCV-Daten fehlen Spalten: {', '.join(missing)}",
                detail=f"Erwartet: {', '.join(required)}",
            )
        if len(df) < WARMUP_CANDLES + 10:
            raise BacktestError(
                f"{prefix}Zu wenige Kerzen fuer einen Backtest: {len(df)} vorhanden, "
                f"mindestens {WARMUP_CANDLES + 10} benoetigt.",
                detail="Zeitraum vergroessern oder kleineren Timeframe waehlen",
            )
        if not df.index.is_monotonic_increasing:
            raise BacktestError(f"{prefix}OHLCV-Daten muessen aufsteigend nach Zeit sortiert sein.")

    def _generate_signal(self, window: pd.DataFrame, index: int) -> SignalResult | None:
        try:
            indicators = self._indicators.compute(
                window, self._config.timeframe, symbol=self._config.symbol, strict=False
            )
            candle_time = ensure_utc(_index_time(window, len(window) - 1))
            market_regime = None
            market_snap = self._market_regime_at(candle_time)
            if self._config.regime_filter_enabled:
                if market_snap is not None and market_snap.available:
                    market_regime = bias_to_market_regime(market_snap.bias)
                else:
                    # Fallback: symbol TF proxy when no BTC series supplied.
                    market_regime = regime_from_indicators(indicators).regime
            result = self._signal_engine.generate(
                self._config.symbol,
                {self._config.timeframe: indicators},
                data_quality=100.0,
                now=candle_time,
                market_regime=market_regime,
            )
            if market_snap is not None and market_snap.available:
                result.coin_score = result.score
                blended = self._regime_engine.score_calculator.blend(
                    result.score, result.direction, market_snap
                )
                result.score = blended.final_score
                result.market_context = market_snap.to_context_dict()
            return result
        except Exception as exc:
            logger.debug("backtest_signal_skipped", index=index, error=str(exc))
            return None

    def _generate_signal_mtf(
        self,
        mtf_frames: dict[str, pd.DataFrame],
        cutoff: datetime,
        index: int,
    ) -> SignalResult | None:
        """Signale mit allen Timeframes bis zum Stichtag erzeugen."""
        try:
            indicator_sets: dict[str, object] = {}
            requested = self._config.timeframes or tuple(mtf_frames)
            for timeframe in requested:
                frame = mtf_frames.get(timeframe)
                if frame is None:
                    continue
                mask = frame.index <= cutoff
                window = frame.loc[mask]
                if len(window) < WARMUP_CANDLES:
                    continue
                indicator_sets[timeframe] = self._indicators.compute(
                    window, timeframe, symbol=self._config.symbol, strict=False
                )

            if not indicator_sets:
                return None

            coverage = len(indicator_sets) / max(len(requested), 1)
            data_quality = 100.0 * coverage
            market_regime = None
            market_snap = self._market_regime_at(cutoff)
            if self._config.regime_filter_enabled:
                if market_snap is not None and market_snap.available:
                    market_regime = bias_to_market_regime(market_snap.bias)
                else:
                    regime_tf = "4h" if "4h" in indicator_sets else self._config.timeframe
                    regime_ind = indicator_sets.get(regime_tf) or next(iter(indicator_sets.values()))
                    market_regime = regime_from_indicators(regime_ind).regime  # type: ignore[arg-type]
            result = self._signal_engine.generate(
                self._config.symbol,
                indicator_sets,  # type: ignore[arg-type]
                data_quality=data_quality,
                now=cutoff,
                market_regime=market_regime,
            )
            if market_snap is not None and market_snap.available:
                result.coin_score = result.score
                blended = self._regime_engine.score_calculator.blend(
                    result.score, result.direction, market_snap
                )
                result.score = blended.final_score
                result.market_context = market_snap.to_context_dict()
            return result
        except Exception as exc:
            logger.debug("backtest_mtf_signal_skipped", index=index, error=str(exc))
            return None

    def _hold_expires_at(self, entry_at: datetime) -> datetime:
        """Management-Fenster ab Fill — wie Paper nach Retest-Activate."""
        return ensure_utc(entry_at) + self._config.expiry_multiplier * timeframe_to_timedelta(
            self._config.timeframe
        )

    def _open_trade(
        self,
        signal: SignalResult,
        df: pd.DataFrame,
        index: int,
        equity: float,
    ) -> SimulatedTrade | None:
        """Trade auf der Eroeffnung der Folgekerze eroeffnen (kein Look-ahead / IST)."""
        if not signal.direction.is_actionable:
            return None

        entry_index = index + 1
        if entry_index >= len(df):
            return None

        raw_entry = float(df["open"].iloc[entry_index])
        entry_price = self._apply_slippage(raw_entry, is_long=signal.direction.is_long)

        risk = signal.risk
        if risk is None:
            return None

        stop = float(risk.stop_loss)
        stop_distance = abs(entry_price - stop)
        if stop_distance <= 0:
            return None

        # TPs am Fill neu verankern (reine R-Leiter) — Signal-TPs waren am Zone-Edge.
        tp1, tp2, tp3 = levels_from_entry_sl(
            Decimal(str(entry_price)),
            Decimal(str(stop)),
            is_long=signal.direction.is_long,
            multipliers=tuple(Decimal(str(m)) for m in self._config.tp_multipliers),
        )

        # Positionsgroesse aus dem Risiko je Trade, begrenzt durch das Kapital.
        risk_amount = equity * (risk.risk_percent / 100.0)
        quantity = risk_amount / stop_distance
        max_quantity = equity / entry_price if entry_price > 0 else 0.0
        quantity = min(quantity, max_quantity)
        if quantity <= 0:
            return None

        entry_at = ensure_utc(_index_time(df, entry_index))
        return SimulatedTrade(
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            direction=signal.direction,
            entry_at=entry_at,
            entry_price=entry_price,
            stop_loss=stop,
            take_profit_1=float(tp1),
            take_profit_2=float(tp2),
            take_profit_3=float(tp3),
            quantity=quantity,
            remaining_quantity=quantity,
            current_stop=stop,
            risk_reward_planned=abs(float(tp2) - entry_price) / stop_distance,
            signal_score=signal.score,
            expires_at=self._hold_expires_at(entry_at),
        )

    def _open_trade_retest(
        self,
        signal: SignalResult,
        df: pd.DataFrame,
        index: int,
        equity: float,
    ) -> SimulatedTrade | None:
        """Entry erst nach ATR-Pullback in die Retest-Zone (Winning Arm B)."""
        if not signal.direction.is_actionable or signal.risk is None:
            return None

        arm_time = ensure_utc(_index_time(df, index))
        # Zone edge like live paper (long=low / short=high), not mid.
        if signal.direction.is_long and signal.risk.entry_low is not None:
            reference = float(signal.risk.entry_low)
        elif (not signal.direction.is_long) and signal.risk.entry_high is not None:
            reference = float(signal.risk.entry_high)
        else:
            reference = float(signal.risk.entry_mid or signal.reference_price)
        candles = _df_to_candles(df, self._config.timeframe)
        arm = arm_retest_entry(
            direction=signal.direction,
            arm_time=arm_time,
            reference_entry=reference,
            original_stop=float(signal.risk.stop_loss),
            timeframe=self._config.timeframe,
            candles=candles,
            config=RetestEntryConfig(
                zone_near=Decimal(str(self._config.retest_zone_near)),
                zone_far=Decimal(str(self._config.retest_zone_far)),
                pending_multiplier=self._config.retest_pending_multiplier,
                min_bars_in_zone=int(self._config.retest_min_bars_in_zone),
                trendline_gate_enabled=bool(self._config.retest_trendline_gate),
                trendline_buffer_atr=float(self._config.retest_trendline_buffer_atr),
                trendline_lookback=int(self._config.retest_trendline_lookback),
                trendline_min_points=int(self._config.retest_trendline_min_points),
                trendline_min_r2=float(self._config.retest_trendline_min_r2),
                trendline_min_clearance_atr=float(
                    self._config.retest_trendline_min_clearance_atr
                ),
            ),
        )
        if not arm.filled or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
            return None

        entry_price = self._apply_slippage(
            float(arm.fill_price), is_long=signal.direction.is_long
        )
        stop = float(arm.stop)
        tp1, tp2, tp3 = levels_from_entry_sl(
            Decimal(str(entry_price)),
            Decimal(str(stop)),
            is_long=signal.direction.is_long,
            multipliers=tuple(Decimal(str(m)) for m in self._config.tp_multipliers),
        )
        stop_distance = abs(entry_price - stop)
        if stop_distance <= 0:
            return None
        risk_amount = equity * (signal.risk.risk_percent / 100.0)
        quantity = risk_amount / stop_distance
        max_quantity = equity / entry_price if entry_price > 0 else 0.0
        quantity = min(quantity, max_quantity)
        if quantity <= 0:
            return None

        return SimulatedTrade(
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            direction=signal.direction,
            entry_at=ensure_utc(arm.fill_time),
            entry_price=entry_price,
            stop_loss=stop,
            take_profit_1=float(tp1),
            take_profit_2=float(tp2),
            take_profit_3=float(tp3),
            quantity=quantity,
            remaining_quantity=quantity,
            current_stop=stop,
            risk_reward_planned=abs(float(tp2) - entry_price) / stop_distance,
            signal_score=signal.score,
            expires_at=self._hold_expires_at(ensure_utc(arm.fill_time)),
        )

    def _process_open_trade(
        self, trade: SimulatedTrade, df: pd.DataFrame, index: int, interval_minutes: int
    ) -> float:
        """Offenen Trade in Kerze ``index`` fortfuehren. Rueckgabe: realisiertes PnL."""
        if self._config.scale_out_enabled:
            return self._process_scale_out(trade, df, index, interval_minutes)
        closed = self._try_close_full(trade, df, index, interval_minutes)
        return trade.net_pnl if closed else 0.0

    def _try_close(
        self, trade: SimulatedTrade, df: pd.DataFrame, index: int, interval_minutes: int
    ) -> bool:
        """Kompatibilitaets-Wrapper: True wenn Trade vollstaendig geschlossen."""
        self._process_open_trade(trade, df, index, interval_minutes)
        return trade.is_closed

    def _try_close_full(
        self, trade: SimulatedTrade, df: pd.DataFrame, index: int, interval_minutes: int
    ) -> bool:
        """All-or-nothing-Exit (legacy)."""
        candle_time = ensure_utc(_index_time(df, index))
        if candle_time <= trade.entry_at:
            return False

        high = float(df["high"].iloc[index])
        low = float(df["low"].iloc[index])
        is_long = trade.direction.is_long

        stop_hit = low <= trade.current_stop if is_long else high >= trade.current_stop
        tp3_hit = high >= trade.take_profit_3 if is_long else low <= trade.take_profit_3
        tp2_hit = high >= trade.take_profit_2 if is_long else low <= trade.take_profit_2
        tp1_hit = high >= trade.take_profit_1 if is_long else low <= trade.take_profit_1

        if stop_hit:
            self._close_remaining(
                trade,
                exit_at=candle_time,
                exit_price=trade.current_stop,
                reason=ExitReason.STOP_LOSS,
                interval_minutes=interval_minutes,
            )
            return True

        for hit, price, reason in (
            (tp3_hit, trade.take_profit_3, ExitReason.TAKE_PROFIT_3),
            (tp2_hit, trade.take_profit_2, ExitReason.TAKE_PROFIT_2),
            (tp1_hit, trade.take_profit_1, ExitReason.TAKE_PROFIT_1),
        ):
            if hit:
                self._close_remaining(
                    trade,
                    exit_at=candle_time,
                    exit_price=price,
                    reason=reason,
                    interval_minutes=interval_minutes,
                )
                return True

        if candle_time >= trade.expires_at:
            self._close_remaining(
                trade,
                exit_at=candle_time,
                exit_price=float(df["close"].iloc[index]),
                reason=ExitReason.EXPIRED,
                interval_minutes=interval_minutes,
            )
            return True

        return False

    def _process_scale_out(
        self, trade: SimulatedTrade, df: pd.DataFrame, index: int, interval_minutes: int
    ) -> float:
        """Teilverkaeufe an TP1/TP2/TP3; nach TP1 Stop auf Break-even."""
        candle_time = ensure_utc(_index_time(df, index))
        if candle_time <= trade.entry_at or trade.remaining_quantity <= 1e-12:
            return 0.0

        high = float(df["high"].iloc[index])
        low = float(df["low"].iloc[index])
        is_long = trade.direction.is_long
        realized = 0.0

        stop_hit = low <= trade.current_stop if is_long else high >= trade.current_stop
        if stop_hit:
            return self._close_remaining(
                trade,
                exit_at=candle_time,
                exit_price=trade.current_stop,
                reason=ExitReason.STOP_LOSS,
                interval_minutes=interval_minutes,
            )

        fractions = self._config.scale_out_fractions
        levels = (
            (not trade.tp1_filled, trade.take_profit_1, ExitReason.TAKE_PROFIT_1, fractions[0], 1),
            (not trade.tp2_filled, trade.take_profit_2, ExitReason.TAKE_PROFIT_2, fractions[1], 2),
            (not trade.tp3_filled, trade.take_profit_3, ExitReason.TAKE_PROFIT_3, fractions[2], 3),
        )

        for pending, price, reason, fraction, level in levels:
            if not pending:
                continue
            hit = high >= price if is_long else low <= price
            if not hit:
                break
            qty = min(trade.quantity * fraction, trade.remaining_quantity)
            if level == 3:
                qty = trade.remaining_quantity
            realized += self._reduce_position(
                trade,
                quantity=qty,
                exit_at=candle_time,
                exit_price=price,
                reason=reason,
                interval_minutes=interval_minutes,
            )
            if level == 1:
                trade.tp1_filled = True
                if self._config.move_stop_to_breakeven_after_tp1:
                    trade.current_stop = RiskManager.fee_aware_breakeven(
                        trade.entry_price,
                        is_long=is_long,
                        fee_percent=self._config.fee_percent,
                    )
                extend_mult = int(self._config.expiry_multiplier_after_tp1)
                if extend_mult > 0:
                    trade.expires_at = candle_time + extend_mult * timeframe_to_timedelta(
                        self._config.timeframe
                    )
            elif level == 2:
                trade.tp2_filled = True
            else:
                trade.tp3_filled = True

            if trade.remaining_quantity <= 1e-12:
                return realized

        if candle_time >= trade.expires_at and trade.remaining_quantity > 1e-12:
            realized += self._close_remaining(
                trade,
                exit_at=candle_time,
                exit_price=float(df["close"].iloc[index]),
                reason=ExitReason.EXPIRED,
                interval_minutes=interval_minutes,
            )

        return realized

    def _reduce_position(
        self,
        trade: SimulatedTrade,
        *,
        quantity: float,
        exit_at: datetime,
        exit_price: float,
        reason: ExitReason,
        interval_minutes: int,
    ) -> float:
        if quantity <= 0 or trade.remaining_quantity <= 0:
            return 0.0

        qty = min(quantity, trade.remaining_quantity)
        adjusted_exit = self._apply_slippage(exit_price, is_long=not trade.direction.is_long)
        direction = 1.0 if trade.direction.is_long else -1.0
        gross = (adjusted_exit - trade.entry_price) * qty * direction
        fee_rate = self._config.fee_percent / 100.0
        # Entry-Gebuehr anteilig + Exit-Gebuehr.
        entry_fee_share = trade.entry_price * qty * fee_rate
        exit_fee = adjusted_exit * qty * fee_rate
        fees = entry_fee_share + exit_fee
        net = gross - fees

        trade.remaining_quantity = max(0.0, trade.remaining_quantity - qty)
        trade.gross_pnl += gross
        trade.fees += fees
        trade.net_pnl += net
        trade.exit_reason = reason
        trade.holding_minutes = max(
            interval_minutes,
            int((exit_at - trade.entry_at).total_seconds() // 60),
        )
        invested = trade.entry_price * trade.quantity
        trade.pnl_percent = (trade.net_pnl / invested * 100.0) if invested > 0 else 0.0
        if trade.remaining_quantity <= 1e-12:
            trade.exit_at = exit_at
            trade.exit_price = adjusted_exit
        return net

    def _close_remaining(
        self,
        trade: SimulatedTrade,
        *,
        exit_at: datetime,
        exit_price: float,
        reason: ExitReason,
        interval_minutes: int,
    ) -> float:
        if trade.remaining_quantity <= 1e-12:
            return 0.0
        return self._reduce_position(
            trade,
            quantity=trade.remaining_quantity,
            exit_at=exit_at,
            exit_price=exit_price,
            reason=reason,
            interval_minutes=interval_minutes,
        )

    def _close_trade(
        self,
        trade: SimulatedTrade,
        *,
        exit_at: datetime,
        exit_price: float,
        reason: ExitReason,
        interval_minutes: int,
    ) -> None:
        """Legacy-API: Restposition schliessen."""
        self._close_remaining(
            trade,
            exit_at=exit_at,
            exit_price=exit_price,
            reason=reason,
            interval_minutes=interval_minutes,
        )

    def _apply_slippage(self, price: float, *, is_long: bool) -> float:
        """Slippage immer zum Nachteil der Position ansetzen."""
        factor = self._config.slippage_percent / 100.0
        return price * (1.0 + factor) if is_long else price * (1.0 - factor)


def _index_time(df: pd.DataFrame, position: int) -> datetime:
    value = df.index[position]
    return value if isinstance(value, datetime) else pd.Timestamp(value).to_pydatetime()


def _df_to_candles(df: pd.DataFrame, timeframe: str) -> list[Candle]:
    delta = timeframe_to_timedelta(timeframe)
    out: list[Candle] = []
    for ts, row in df.iterrows():
        open_time = ensure_utc(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
        out.append(
            Candle(
                open_time=open_time,
                close_time=open_time + delta,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                is_closed=True,
            )
        )
    return out
