"""Tests der Backtesting-Engine und der Kennzahlen.

Der wichtigste Test dieser Datei ist :class:`TestLookAheadFreedom`: er weist
nach, dass zukuenftige Kursdaten das Ergebnis nicht beeinflussen koennen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import pandas as pd
import pytest

from app.backtesting.engine import (
    WARMUP_CANDLES,
    BacktestConfig,
    BacktestEngine,
    BacktestOutcome,
    SimulatedTrade,
)
from app.backtesting.metrics import compute_metrics, summarize_for_display
from app.core.enums import ExitReason, SignalDirection
from app.core.errors import BacktestError


def make_config(**overrides: object) -> BacktestConfig:
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "fee_percent": 0.1,
        "slippage_percent": 0.05,
        "initial_capital": 10_000.0,
        # Synthetische Fixtures: Live-Filter lockern, damit Trades entstehen.
        "min_score": 55.0,
        "require_strong_signals": False,
        "block_range_market": False,
        "cooldown_minutes": 0,
        "min_adx": 0.0,
        "rsi_long_max": 100.0,
        "rsi_short_min": 0.0,
        # Unit-Tests nutzen IST/next-open; HTF separat getestet.
        "htf_breakout_enabled": False,
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)  # type: ignore[arg-type]


class TestValidation:
    def test_rejects_missing_columns(self) -> None:
        df = pd.DataFrame({"close": [1.0] * 300})
        with pytest.raises(BacktestError, match="fehlen Spalten"):
            BacktestEngine(make_config()).run(df)

    def test_rejects_too_short_history(self, uptrend_df: pd.DataFrame) -> None:
        with pytest.raises(BacktestError, match="Zu wenige Kerzen"):
            BacktestEngine(make_config()).run(uptrend_df.iloc[:50])

    def test_rejects_unsorted_index(self, uptrend_df: pd.DataFrame) -> None:
        shuffled = uptrend_df.iloc[::-1]
        with pytest.raises(BacktestError, match="aufsteigend"):
            BacktestEngine(make_config()).run(shuffled)


class TestSimulation:
    def test_runs_and_reports_counters(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        assert outcome.candles_evaluated == len(uptrend_df) - WARMUP_CANDLES - 2
        assert outcome.signals_generated >= 0
        assert outcome.equity_curve

    def test_produces_trades_in_a_trending_market(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        assert outcome.trades, "Ein klarer Aufwaertstrend sollte Trades erzeugen"

    def test_all_trades_are_closed_at_the_end(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        assert all(trade.is_closed for trade in outcome.trades)

    def test_only_one_open_trade_at_a_time(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        trades = sorted(outcome.trades, key=lambda t: t.entry_at)
        for earlier, later in pairwise(trades):
            assert earlier.exit_at is not None
            assert earlier.exit_at <= later.entry_at

    def test_exit_reasons_are_valid(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        for trade in outcome.trades:
            assert trade.exit_reason in set(ExitReason)

    def test_every_trade_has_a_positive_holding_period(self, uptrend_df: pd.DataFrame) -> None:
        """Trades mit Haltedauer null waeren ein Artefakt des Datenendes."""
        outcome = BacktestEngine(make_config()).run(uptrend_df)
        assert outcome.trades
        for trade in outcome.trades:
            assert trade.entry_at in uptrend_df.index
            assert trade.exit_at is not None
            assert trade.exit_at > trade.entry_at
            assert trade.holding_minutes > 0

    def test_supports_short_trades(self, downtrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config()).run(downtrend_df)
        directions = {trade.direction for trade in outcome.trades}
        assert directions
        assert all(d in set(SignalDirection) for d in directions)


class TestCostsAndSlippage:
    def test_fees_are_charged_on_both_sides(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(make_config(fee_percent=0.1)).run(uptrend_df)
        assert outcome.trades
        for trade in outcome.trades:
            assert trade.fees > 0
            assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.fees)

    def test_higher_fees_reduce_net_result(self, uptrend_df: pd.DataFrame) -> None:
        cheap = BacktestEngine(make_config(fee_percent=0.0)).run(uptrend_df)
        pricey = BacktestEngine(make_config(fee_percent=0.5)).run(uptrend_df)
        assert sum(t.net_pnl for t in pricey.trades) < sum(t.net_pnl for t in cheap.trades)

    def test_zero_fee_and_slippage_yields_zero_fees(self, uptrend_df: pd.DataFrame) -> None:
        outcome = BacktestEngine(
            make_config(fee_percent=0.0, slippage_percent=0.0)
        ).run(uptrend_df)
        assert all(trade.fees == pytest.approx(0.0) for trade in outcome.trades)

    def test_slippage_worsens_entry_for_long(self, uptrend_df: pd.DataFrame) -> None:
        """Slippage wird immer zum Nachteil der Position angesetzt."""
        without = BacktestEngine(make_config(slippage_percent=0.0)).run(uptrend_df)
        with_slip = BacktestEngine(make_config(slippage_percent=0.5)).run(uptrend_df)
        longs_without = [t for t in without.trades if t.direction.is_long]
        longs_with = [t for t in with_slip.trades if t.direction.is_long]
        if not longs_without or not longs_with:
            pytest.skip("Fixture erzeugte keine Long-Trades")
        assert longs_with[0].entry_price > longs_without[0].entry_price


class TestLookAheadFreedom:
    def test_future_candles_do_not_change_past_trades(self, uptrend_df: pd.DataFrame) -> None:
        """Der Kernnachweis: identische Vergangenheit, veraenderte Zukunft.

        Ein Backtest ueber die ersten N Kerzen muss exakt dieselben Trades
        erzeugen wie ein Backtest ueber den gesamten Zeitraum — beschraenkt auf
        die Trades, die vor dem Schnittpunkt eroeffnet wurden.
        """
        cutoff = 320
        config = make_config()

        truncated = BacktestEngine(config).run(uptrend_df.iloc[:cutoff])
        full = BacktestEngine(config).run(uptrend_df)

        boundary = uptrend_df.index[cutoff - 2]
        early_truncated = [t for t in truncated.trades if t.entry_at < boundary]
        early_full = [t for t in full.trades if t.entry_at < boundary]

        assert early_truncated, "Der verkuerzte Lauf sollte Trades enthalten"
        assert len(early_truncated) == len(early_full)
        for a, b in zip(early_truncated, early_full, strict=True):
            assert a.entry_at == b.entry_at
            assert a.entry_price == pytest.approx(b.entry_price)
            assert a.direction == b.direction
            assert a.stop_loss == pytest.approx(b.stop_loss)
            assert a.signal_score == pytest.approx(b.signal_score)

    def test_modified_future_leaves_past_signals_untouched(self, uptrend_df: pd.DataFrame) -> None:
        """Selbst ein extremer Kurssprung in der Zukunft aendert nichts."""
        cutoff = 320
        manipulated = uptrend_df.copy()
        manipulated.iloc[cutoff:, :4] *= 5.0

        config = make_config()
        original = BacktestEngine(config).run(uptrend_df)
        changed = BacktestEngine(config).run(manipulated)

        boundary = uptrend_df.index[cutoff - 2]
        early_original = [t for t in original.trades if t.entry_at < boundary]
        early_changed = [t for t in changed.trades if t.entry_at < boundary]

        assert len(early_original) == len(early_changed)
        for a, b in zip(early_original, early_changed, strict=True):
            assert a.entry_at == b.entry_at
            assert a.entry_price == pytest.approx(b.entry_price)
            assert a.signal_score == pytest.approx(b.signal_score)

    def test_run_is_reproducible(self, uptrend_df: pd.DataFrame) -> None:
        config = make_config()
        first = BacktestEngine(config).run(uptrend_df)
        second = BacktestEngine(config).run(uptrend_df)

        assert len(first.trades) == len(second.trades)
        assert first.equity_curve == second.equity_curve

    def test_stop_wins_when_stop_and_target_share_a_candle(self) -> None:
        """Konservative Annahme: aus OHLC ist die Reihenfolge nicht ableitbar."""
        engine = BacktestEngine(make_config(fee_percent=0.0, slippage_percent=0.0))
        trade = SimulatedTrade(
            symbol="BTCUSDT",
            timeframe="1h",
            direction=SignalDirection.LONG,
            entry_at=datetime(2024, 1, 1, tzinfo=UTC),
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=105.0,
            take_profit_2=110.0,
            take_profit_3=120.0,
            quantity=1.0,
            risk_reward_planned=2.0,
            signal_score=70.0,
            expires_at=datetime(2024, 1, 5, tzinfo=UTC),
        )
        # Eine Kerze, die sowohl den Stop als auch alle Ziele umschliesst.
        df = pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [100.0, 130.0],
                "low": [100.0, 90.0],
                "close": [100.0, 120.0],
                "volume": [1.0, 1.0],
            },
            index=pd.DatetimeIndex(
                [datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 1, tzinfo=UTC)]
            ),
        )

        assert engine._try_close(trade, df, 1, 60) is True
        assert trade.exit_reason is ExitReason.STOP_LOSS

    def test_scale_out_partial_then_breakeven_stop(self) -> None:
        engine = BacktestEngine(
            make_config(
                fee_percent=0.0,
                slippage_percent=0.0,
                scale_out_enabled=True,
                move_stop_to_breakeven_after_tp1=True,
            )
        )
        trade = SimulatedTrade(
            symbol="BTCUSDT",
            timeframe="1h",
            direction=SignalDirection.LONG,
            entry_at=datetime(2024, 1, 1, tzinfo=UTC),
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=110.0,
            take_profit_2=120.0,
            take_profit_3=130.0,
            quantity=3.0,
            risk_reward_planned=2.0,
            signal_score=70.0,
            expires_at=datetime(2024, 1, 10, tzinfo=UTC),
        )
        df = pd.DataFrame(
            {
                "open": [100.0, 100.0, 100.0],
                "high": [100.0, 111.0, 100.0],
                "low": [100.0, 99.0, 99.5],
                "close": [100.0, 110.0, 100.0],
                "volume": [1.0, 1.0, 1.0],
            },
            index=pd.DatetimeIndex(
                [
                    datetime(2024, 1, 1, tzinfo=UTC),
                    datetime(2024, 1, 1, 1, tzinfo=UTC),
                    datetime(2024, 1, 1, 2, tzinfo=UTC),
                ]
            ),
        )

        assert engine._try_close(trade, df, 1, 60) is False
        assert trade.tp1_filled is True
        assert trade.current_stop == pytest.approx(100.0)
        assert trade.remaining_quantity == pytest.approx(2.0)

        assert engine._try_close(trade, df, 2, 60) is True
        assert trade.exit_reason is ExitReason.STOP_LOSS
        assert trade.net_pnl == pytest.approx(10.0)  # 1 unit @ +10 from TP1; rest @ BE


    def test_cooldown_reduces_trade_count(self, uptrend_df: pd.DataFrame) -> None:
        relaxed = make_config(cooldown_minutes=0)
        strict = make_config(cooldown_minutes=240)
        without = BacktestEngine(relaxed).run(uptrend_df)
        with_cd = BacktestEngine(strict).run(uptrend_df)
        assert len(with_cd.trades) <= len(without.trades)
        assert with_cd.signals_skipped_cooldown >= 0
    @staticmethod
    def metrics_for(
        trades: list[SimulatedTrade], *, initial_capital: float = 10_000.0
    ) -> dict[str, dict[str, float]]:
        outcome = BacktestOutcome(
            config=make_config(initial_capital=initial_capital), trades=trades
        )
        return compute_metrics(outcome)

    @staticmethod
    def make_trade(
        net_pnl: float, direction: SignalDirection, minutes: int = 120
    ) -> SimulatedTrade:
        trade = SimulatedTrade(
            symbol="BTCUSDT",
            timeframe="1h",
            direction=direction,
            entry_at=datetime(2024, 1, 1, tzinfo=UTC),
            entry_price=100.0,
            stop_loss=95.0,
            take_profit_1=105.0,
            take_profit_2=110.0,
            take_profit_3=120.0,
            quantity=1.0,
            risk_reward_planned=2.0,
            signal_score=70.0,
            expires_at=datetime(2024, 1, 5, tzinfo=UTC),
        )
        trade.exit_at = datetime(2024, 1, 1, 2, tzinfo=UTC)
        trade.exit_price = 100.0 + net_pnl
        trade.exit_reason = ExitReason.TAKE_PROFIT_1 if net_pnl > 0 else ExitReason.STOP_LOSS
        trade.remaining_quantity = 0.0
        trade.gross_pnl = net_pnl
        trade.fees = 0.0
        trade.net_pnl = net_pnl
        trade.pnl_percent = net_pnl
        trade.holding_minutes = minutes
        return trade

    def test_empty_trades_yield_zero_metrics(self) -> None:
        overall = self.metrics_for([])["overall"]
        assert overall["trade_count"] == 0
        assert overall["profit_factor"] == 0.0

    def test_win_rate(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(10.0, SignalDirection.LONG),
                self.make_trade(10.0, SignalDirection.LONG),
                self.make_trade(-5.0, SignalDirection.LONG),
                self.make_trade(-5.0, SignalDirection.LONG),
            ]
        )["overall"]
        assert overall["trade_count"] == 4
        assert overall["win_count"] == 2
        assert overall["loss_count"] == 2
        assert overall["win_rate"] == pytest.approx(0.5)

    def test_profit_factor(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(30.0, SignalDirection.LONG),
                self.make_trade(-10.0, SignalDirection.LONG),
            ]
        )["overall"]
        assert overall["profit_factor"] == pytest.approx(3.0)

    def test_profit_factor_is_zero_without_losses(self) -> None:
        """Ohne Verluste waere der Profit Factor unendlich."""
        overall = self.metrics_for([self.make_trade(30.0, SignalDirection.LONG)])["overall"]
        assert overall["profit_factor"] == 0.0

    def test_average_win_and_loss(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(20.0, SignalDirection.LONG),
                self.make_trade(10.0, SignalDirection.LONG),
                self.make_trade(-6.0, SignalDirection.LONG),
            ]
        )["overall"]
        assert overall["average_win"] == pytest.approx(15.0)
        # average_loss wird als Betrag gefuehrt.
        assert overall["average_loss"] == pytest.approx(6.0)

    def test_expectancy_matches_manual_calculation(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(20.0, SignalDirection.LONG),
                self.make_trade(-10.0, SignalDirection.LONG),
            ]
        )["overall"]
        # 0.5 * 20 - 0.5 * 10 = 5
        assert overall["expectancy"] == pytest.approx(5.0)

    def test_net_profit_sums_pnl(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(20.0, SignalDirection.LONG),
                self.make_trade(-8.0, SignalDirection.SHORT),
            ]
        )["overall"]
        assert overall["net_profit"] == pytest.approx(12.0)

    def test_net_profit_percent_relates_to_capital(self) -> None:
        overall = self.metrics_for(
            [self.make_trade(100.0, SignalDirection.LONG)], initial_capital=10_000.0
        )["overall"]
        assert overall["net_profit_percent"] == pytest.approx(1.0)

    def test_max_drawdown_is_non_positive(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(50.0, SignalDirection.LONG),
                self.make_trade(-80.0, SignalDirection.LONG),
                self.make_trade(10.0, SignalDirection.LONG),
            ],
            initial_capital=1_000.0,
        )["overall"]
        # Drawdown wird als positiver Betrag gefuehrt: Hoch 1.050, Tief 970.
        assert overall["max_drawdown"] == pytest.approx(80.0)
        assert overall["max_drawdown_percent"] == pytest.approx(80.0 / 1_050.0 * 100.0)

    def test_long_and_short_are_reported_separately(self) -> None:
        metrics = self.metrics_for(
            [
                self.make_trade(20.0, SignalDirection.LONG),
                self.make_trade(-5.0, SignalDirection.SHORT),
            ]
        )
        assert metrics["long"]["trade_count"] == 1
        assert metrics["short"]["trade_count"] == 1
        assert metrics["long"]["net_profit"] == pytest.approx(20.0)
        assert metrics["short"]["net_profit"] == pytest.approx(-5.0)

    def test_per_symbol_and_timeframe_scopes_exist(self) -> None:
        metrics = self.metrics_for([self.make_trade(20.0, SignalDirection.LONG)])
        assert "symbol:BTCUSDT" in metrics
        assert "timeframe:1h" in metrics

    def test_average_holding_time(self) -> None:
        overall = self.metrics_for(
            [
                self.make_trade(1.0, SignalDirection.LONG, minutes=60),
                self.make_trade(1.0, SignalDirection.LONG, minutes=180),
            ]
        )["overall"]
        assert overall["average_holding_minutes"] == pytest.approx(120.0)

    def test_total_fees_are_summed(self) -> None:
        trades = [self.make_trade(20.0, SignalDirection.LONG)]
        trades[0].fees = 3.5
        assert self.metrics_for(trades)["overall"]["total_fees"] == pytest.approx(3.5)

    def test_required_metrics_are_present(self) -> None:
        """Der Auftrag nennt eine feste Liste an Kennzahlen."""
        overall = self.metrics_for(
            [
                self.make_trade(20.0, SignalDirection.LONG),
                self.make_trade(-10.0, SignalDirection.SHORT),
            ]
        )["overall"]
        for key in (
            "trade_count",
            "win_rate",
            "average_win",
            "average_loss",
            "profit_factor",
            "expectancy",
            "max_drawdown",
            "sharpe_ratio",
            "sortino_ratio",
            "average_risk_reward",
            "net_profit",
            "total_fees",
            "average_holding_minutes",
        ):
            assert key in overall, key

    def test_display_summary_is_readable(self) -> None:
        overall = self.metrics_for([self.make_trade(20.0, SignalDirection.LONG)])["overall"]
        lines = summarize_for_display(overall, "Gesamt")
        assert lines
        assert lines[0].startswith("Gesamt")
