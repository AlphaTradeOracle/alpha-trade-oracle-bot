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

import pandas as pd

from app.core.enums import ExitReason, SignalDirection
from app.core.errors import BacktestError
from app.core.logging import get_logger
from app.core.time import ensure_utc, timeframe_minutes
from app.indicators.engine import IndicatorEngine
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.signals.risk import RiskConfig, RiskManager
from app.signals.types import SignalResult
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

logger = get_logger(__name__)

#: Kerzen, die vor dem ersten Signal fuer die Indikator-Aufwaermphase noetig sind.
WARMUP_CANDLES = 210


@dataclass(frozen=True)
class BacktestConfig:
    """Parameter eines Backtests."""

    symbol: str
    timeframe: str
    fee_percent: float = 0.1
    slippage_percent: float = 0.05
    initial_capital: float = 10_000.0
    min_score: float = 65.0
    min_risk_reward_ratio: float = 2.0
    atr_multiplier: float = 1.5
    max_atr_percent: float = 12.0
    expiry_multiplier: int = 4
    #: Nur ein Trade gleichzeitig — ohne Positionsverwaltung waere die
    #: Kapitalkurve nicht interpretierbar.
    allow_concurrent_trades: bool = False
    weights: StrategyWeights = DEFAULT_WEIGHTS


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
    exit_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    pnl_percent: float = 0.0
    holding_minutes: int = 0

    @property
    def is_closed(self) -> bool:
        return self.exit_at is not None

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


class BacktestEngine:
    """Simuliert die Live-Logik auf historischen Daten."""

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._indicators = IndicatorEngine(min_candles=WARMUP_CANDLES)
        self._signal_engine = SignalEngine(
            SignalEngineConfig(
                weights=config.weights,
                primary_timeframe=config.timeframe,
                confirmation_timeframe=config.timeframe,
                min_risk_reward_ratio=config.min_risk_reward_ratio,
                max_atr_percent=config.max_atr_percent,
                expiry_multiplier=config.expiry_multiplier,
                enable_sentiment=False,
            ),
            RiskManager(
                RiskConfig(
                    atr_multiplier=config.atr_multiplier,
                    min_risk_reward_ratio=config.min_risk_reward_ratio,
                    reference_capital=config.initial_capital,
                )
            ),
        )

    def run(self, df: pd.DataFrame) -> BacktestOutcome:
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
        interval_minutes = timeframe_minutes(self._config.timeframe)
        total = len(df)

        # Der Einstieg erfolgt auf Kerze i + 1, die Ausstiegspruefung fruehestens
        # auf i + 2. Beide muessen existieren, sonst entstuende ein Trade mit
        # Haltedauer null, der die Statistik verzerrt.
        for i in range(WARMUP_CANDLES, total - 2):
            outcome.candles_evaluated += 1

            if open_trade is not None:
                closed = self._try_close(open_trade, df, i, interval_minutes)
                if closed:
                    equity += open_trade.net_pnl
                    outcome.equity_curve.append(equity)
                    open_trade = None

            if open_trade is not None and not self._config.allow_concurrent_trades:
                continue

            # Kernpunkt der Look-ahead-Freiheit: nur Daten bis einschliesslich i.
            window = df.iloc[: i + 1]
            signal = self._generate_signal(window, i)
            if signal is None:
                continue

            outcome.signals_generated += 1
            if signal.score < self._config.min_score and signal.direction.is_long:
                outcome.signals_skipped_below_score += 1
                continue
            if signal.direction.is_short and (100.0 - signal.score) < self._config.min_score:
                outcome.signals_skipped_below_score += 1
                continue
            if signal.risk is None:
                continue

            trade = self._open_trade(signal, df, i, equity)
            if trade is not None:
                open_trade = trade
                outcome.trades.append(trade)

        # Offener Trade am Datenende schliessen, damit die Statistik konsistent ist.
        if open_trade is not None and not open_trade.is_closed:
            self._close_trade(
                open_trade,
                exit_at=ensure_utc(_index_time(df, total - 1)),
                exit_price=float(df["close"].iloc[-1]),
                reason=ExitReason.END_OF_DATA,
                interval_minutes=interval_minutes,
            )
            equity += open_trade.net_pnl
            outcome.equity_curve.append(equity)

        logger.info(
            "backtest_completed",
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            candles=outcome.candles_evaluated,
            signals=outcome.signals_generated,
            trades=len(outcome.trades),
        )
        return outcome

    # --- Teilschritte -----------------------------------------------------

    def _validate(self, df: pd.DataFrame) -> None:
        required = ("open", "high", "low", "close", "volume")
        missing = [column for column in required if column not in df.columns]
        if missing:
            raise BacktestError(
                f"OHLCV-Daten fehlen Spalten: {', '.join(missing)}",
                detail=f"Erwartet: {', '.join(required)}",
            )
        if len(df) < WARMUP_CANDLES + 10:
            raise BacktestError(
                f"Zu wenige Kerzen fuer einen Backtest: {len(df)} vorhanden, "
                f"mindestens {WARMUP_CANDLES + 10} benoetigt.",
                detail="Zeitraum vergroessern oder kleineren Timeframe waehlen",
            )
        if not df.index.is_monotonic_increasing:
            raise BacktestError("OHLCV-Daten muessen aufsteigend nach Zeit sortiert sein.")

    def _generate_signal(self, window: pd.DataFrame, index: int):  # type: ignore[no-untyped-def]
        try:
            indicators = self._indicators.compute(
                window, self._config.timeframe, symbol=self._config.symbol, strict=False
            )
            candle_time = ensure_utc(_index_time(window, len(window) - 1))
            return self._signal_engine.generate(
                self._config.symbol,
                {self._config.timeframe: indicators},
                data_quality=100.0,
                now=candle_time,
            )
        except Exception as exc:
            logger.debug("backtest_signal_skipped", index=index, error=str(exc))
            return None

    def _open_trade(
        self,
        signal: SignalResult,
        df: pd.DataFrame,
        index: int,
        equity: float,
    ) -> SimulatedTrade | None:
        """Trade auf der Eroeffnung der Folgekerze eroeffnen (kein Look-ahead)."""
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

        stop_distance = abs(entry_price - risk.stop_loss)
        if stop_distance <= 0:
            return None

        # Positionsgroesse aus dem Risiko je Trade, begrenzt durch das Kapital.
        risk_amount = equity * (risk.risk_percent / 100.0)
        quantity = risk_amount / stop_distance
        max_quantity = equity / entry_price if entry_price > 0 else 0.0
        quantity = min(quantity, max_quantity)
        if quantity <= 0:
            return None

        return SimulatedTrade(
            symbol=self._config.symbol,
            timeframe=self._config.timeframe,
            direction=signal.direction,
            entry_at=ensure_utc(_index_time(df, entry_index)),
            entry_price=entry_price,
            stop_loss=risk.stop_loss,
            take_profit_1=risk.take_profit_1,
            take_profit_2=risk.take_profit_2,
            take_profit_3=risk.take_profit_3,
            quantity=quantity,
            risk_reward_planned=risk.risk_reward_ratio,
            signal_score=signal.score,
            expires_at=signal.expires_at,
        )

    def _try_close(
        self, trade: SimulatedTrade, df: pd.DataFrame, index: int, interval_minutes: int
    ) -> bool:
        """Pruefen, ob der Trade in der Kerze ``index`` geschlossen wird."""
        candle_time = ensure_utc(_index_time(df, index))
        if candle_time <= trade.entry_at:
            return False

        high = float(df["high"].iloc[index])
        low = float(df["low"].iloc[index])
        is_long = trade.direction.is_long

        stop_hit = low <= trade.stop_loss if is_long else high >= trade.stop_loss
        tp3_hit = high >= trade.take_profit_3 if is_long else low <= trade.take_profit_3
        tp2_hit = high >= trade.take_profit_2 if is_long else low <= trade.take_profit_2
        tp1_hit = high >= trade.take_profit_1 if is_long else low <= trade.take_profit_1

        # Konservative Annahme: treffen Stop und Ziel in derselben Kerze, gilt der
        # Stop. Aus OHLC laesst sich die Reihenfolge nicht rekonstruieren, und die
        # optimistische Annahme wuerde die Ergebnisse systematisch verschoenern.
        if stop_hit:
            self._close_trade(
                trade,
                exit_at=candle_time,
                exit_price=trade.stop_loss,
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
                self._close_trade(
                    trade,
                    exit_at=candle_time,
                    exit_price=price,
                    reason=reason,
                    interval_minutes=interval_minutes,
                )
                return True

        if candle_time >= trade.expires_at:
            self._close_trade(
                trade,
                exit_at=candle_time,
                exit_price=float(df["close"].iloc[index]),
                reason=ExitReason.EXPIRED,
                interval_minutes=interval_minutes,
            )
            return True

        return False

    def _close_trade(
        self,
        trade: SimulatedTrade,
        *,
        exit_at: datetime,
        exit_price: float,
        reason: ExitReason,
        interval_minutes: int,
    ) -> None:
        adjusted_exit = self._apply_slippage(exit_price, is_long=not trade.direction.is_long)

        direction = 1.0 if trade.direction.is_long else -1.0
        trade.exit_at = exit_at
        trade.exit_price = adjusted_exit
        trade.exit_reason = reason
        trade.gross_pnl = (adjusted_exit - trade.entry_price) * trade.quantity * direction

        # Gebuehren fallen auf beiden Seiten an.
        fee_rate = self._config.fee_percent / 100.0
        trade.fees = (
            trade.entry_price * trade.quantity + adjusted_exit * trade.quantity
        ) * fee_rate
        trade.net_pnl = trade.gross_pnl - trade.fees

        invested = trade.entry_price * trade.quantity
        trade.pnl_percent = (trade.net_pnl / invested * 100.0) if invested > 0 else 0.0
        trade.holding_minutes = max(
            interval_minutes,
            int((exit_at - trade.entry_at).total_seconds() // 60),
        )

    def _apply_slippage(self, price: float, *, is_long: bool) -> float:
        """Slippage immer zum Nachteil der Position ansetzen."""
        factor = self._config.slippage_percent / 100.0
        return price * (1.0 + factor) if is_long else price * (1.0 - factor)


def _index_time(df: pd.DataFrame, position: int) -> datetime:
    value = df.index[position]
    return value if isinstance(value, datetime) else pd.Timestamp(value).to_pydatetime()
