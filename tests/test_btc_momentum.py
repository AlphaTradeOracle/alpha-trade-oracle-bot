"""Unit tests for BTC rising-momentum short gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.enums import MarketPhase, SignalDirection
from app.indicators.engine import IndicatorSet
from app.signals.btc_momentum import (
    BtcRiseThresholds,
    btc_rising_short_block_reason,
    compute_btc_rise_metrics,
)
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.signals.types import RiskParameters


@dataclass
class FakeCandle:
    open: float
    high: float
    low: float
    close: float
    is_closed: bool = True
    open_time: datetime | None = None


def _1h_series(
    changes: list[float],
    *,
    start: float = 100.0,
    open_bar: float | None = None,
) -> list[FakeCandle]:
    """Build closed 1h candles from successive bar changes in percent."""
    candles: list[FakeCandle] = []
    price = start
    t0 = datetime(2026, 8, 3, 0, tzinfo=UTC)
    for i, chg in enumerate(changes):
        o = price
        c = o * (1.0 + chg / 100.0)
        candles.append(
            FakeCandle(
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                is_closed=True,
                open_time=t0 + timedelta(hours=i),
            )
        )
        price = c
    if open_bar is not None:
        o = price
        c = o * (1.0 + open_bar / 100.0)
        candles.append(
            FakeCandle(
                open=o,
                high=max(o, c),
                low=min(o, c),
                close=c,
                is_closed=False,
                open_time=t0 + timedelta(hours=len(changes)),
            )
        )
    return candles


class TestBtcRiseMetrics:
    def test_green_1h_block(self) -> None:
        c1 = _1h_series([0.0, 0.0, 0.20])  # last 1h +0.20% >= 0.15
        reason = btc_rising_short_block_reason(c1, None)
        assert reason is not None
        assert "BTC rising momentum" in reason
        assert "1h=" in reason

    def test_red_1h_without_other_rise_no_block(self) -> None:
        c1 = _1h_series([-0.05, -0.10, -0.20])
        c4 = [FakeCandle(open=100.0, high=100.0, low=99.0, close=99.5)]  # -0.5%
        reason = btc_rising_short_block_reason(c1, c4)
        assert reason is None

    def test_only_3h_rise_blocks(self) -> None:
        c1 = _1h_series([0.12, 0.12, 0.12])
        metrics = compute_btc_rise_metrics(c1, None)
        assert metrics.pct_1h is not None and metrics.pct_1h < 0.15
        assert metrics.pct_3h is not None and metrics.pct_3h >= 0.35
        reason = btc_rising_short_block_reason(c1, None)
        assert reason is not None
        assert "3h=" in reason

    def test_missing_candles_returns_none(self) -> None:
        assert btc_rising_short_block_reason(None, None) is None
        assert btc_rising_short_block_reason([], []) is None

    def test_ignores_open_1h_bar(self) -> None:
        c1 = _1h_series([-0.10, -0.10, -0.10], open_bar=1.0)
        reason = btc_rising_short_block_reason(c1, None)
        assert reason is None

    def test_4h_threshold_boundary(self) -> None:
        c4_hit = [FakeCandle(open=100.0, high=101.0, low=100.0, close=100.301)]
        c4_miss = [FakeCandle(open=100.0, high=101.0, low=100.0, close=100.299)]
        assert btc_rising_short_block_reason(None, c4_hit) is not None
        assert btc_rising_short_block_reason(None, c4_miss) is None

    def test_1h_threshold_boundary(self) -> None:
        hit = _1h_series([0.0, 0.0, 0.15])
        miss = _1h_series([0.0, 0.0, 0.149])
        assert btc_rising_short_block_reason(hit, None) is not None
        assert btc_rising_short_block_reason(miss, None) is None

    def test_6h_rise_blocks(self) -> None:
        c1 = _1h_series([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.55])
        thresholds = BtcRiseThresholds(
            use_1h=False, use_3h=False, use_4h=False, use_6h=True, pct_6h=0.50
        )
        reason = btc_rising_short_block_reason(c1, None, thresholds=thresholds)
        assert reason is not None

    def test_disabled_returns_none(self) -> None:
        c1 = _1h_series([0.0, 0.0, 1.0])
        reason = btc_rising_short_block_reason(
            c1, None, thresholds=BtcRiseThresholds(enabled=False)
        )
        assert reason is None


class TestEngineBtcRiseGate:
    def test_check_no_trade_short_only(self) -> None:
        engine = SignalEngine(SignalEngineConfig(regime_filter_enabled=False))
        reason = (
            "BTC rising momentum — no new short entries "
            "(1h=+1.00%, 3h=n/a, 4h=n/a, 6h=n/a)"
        )
        indicators = IndicatorSet(
            timeframe="1h",
            candle_open_time=datetime(2026, 8, 3, 12, tzinfo=UTC),
            close_price=1.0,
            adx_14=40.0,
            rsi_14=50.0,
            atr_percent=1.0,
        )
        risk = RiskParameters(
            entry_low=0.99,
            entry_high=1.01,
            stop_loss=1.05,
            take_profit_1=0.97,
            take_profit_2=0.95,
            take_profit_3=0.92,
            risk_reward_ratio=3.0,
            risk_percent=1.0,
            suggested_position_size=1.0,
            stop_distance_percent=1.0,
            invalidation_note="test",
        )
        blocked = engine._check_no_trade(
            SignalDirection.SHORT,
            indicators,
            risk,
            100.0,
            market_phase=MarketPhase.DOWNTREND,
            score=25.0,
            btc_rise_block_reason=reason,
        )
        assert blocked == reason

        long_ok = engine._check_no_trade(
            SignalDirection.LONG,
            indicators,
            risk,
            100.0,
            market_phase=MarketPhase.UPTREND,
            score=80.0,
            btc_rise_block_reason=reason,
        )
        assert long_ok != reason
