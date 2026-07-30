"""Unit tests for HTF breakout confirmation thesis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import SignalDirection
from app.core.time import timeframe_to_timedelta
from app.market_data.types import Candle
from app.signals.htf_breakout import HtfBreakoutConfig, arm_htf_breakout


def _c(open_time: datetime, high: float, low: float, close: float) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + timeframe_to_timedelta("4h"),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        is_closed=True,
    )


def _history(n: int = 40, *, base: float = 100.0) -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(n):
        t = start + timedelta(hours=4 * i)
        # Flat range 95–105 so resistance=105, support=95
        out.append(_c(t, high=105.0, low=95.0, close=base))
    return out


class TestArmHtfBreakout:
    def test_long_fills_on_4h_close_above_resistance(self) -> None:
        candles = _history(30)
        arm_time = candles[20].open_time
        # Breakout bar after signal
        breakout_t = candles[20].open_time + timedelta(hours=4)
        candles.append(_c(breakout_t, high=110.0, low=104.0, close=108.0))

        arm = arm_htf_breakout(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            original_stop=90.0,
            candles_4h=candles,
            config=HtfBreakoutConfig(lookback_bars=20, pending_days=14),
        )
        assert arm.filled
        assert arm.fill_price == pytest.approx(108.0)
        assert arm.level == pytest.approx(105.0)
        assert arm.stop is not None
        assert arm.stop < arm.fill_price

    def test_skips_when_sl_hit_before_confirm(self) -> None:
        candles = _history(30)
        arm_time = candles[20].open_time
        sl_bar_t = candles[20].open_time + timedelta(hours=4)
        candles.append(_c(sl_bar_t, high=100.0, low=89.0, close=92.0))

        arm = arm_htf_breakout(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            original_stop=90.0,
            candles_4h=candles,
            config=HtfBreakoutConfig(lookback_bars=20, pending_days=14),
        )
        assert arm.status == "skipped_sl"

    def test_pending_when_no_confirm_yet(self) -> None:
        candles = _history(30)
        arm_time = candles[20].open_time
        # Only continuation inside range
        nxt = candles[20].open_time + timedelta(hours=4)
        candles.append(_c(nxt, high=104.0, low=96.0, close=100.0))

        arm = arm_htf_breakout(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            original_stop=90.0,
            candles_4h=candles,
            config=HtfBreakoutConfig(lookback_bars=20, pending_days=14),
        )
        assert arm.status == "pending"

    def test_short_fills_on_4h_close_below_support(self) -> None:
        candles = _history(30)
        arm_time = candles[20].open_time
        breakout_t = candles[20].open_time + timedelta(hours=4)
        candles.append(_c(breakout_t, high=96.0, low=90.0, close=92.0))

        arm = arm_htf_breakout(
            direction=SignalDirection.STRONG_SHORT,
            arm_time=arm_time,
            original_stop=110.0,
            candles_4h=candles,
            config=HtfBreakoutConfig(lookback_bars=20, pending_days=14),
        )
        assert arm.filled
        assert arm.fill_price == pytest.approx(92.0)
        assert arm.level == pytest.approx(95.0)
        assert arm.stop is not None
        assert arm.stop > arm.fill_price
