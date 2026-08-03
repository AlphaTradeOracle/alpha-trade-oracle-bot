"""Diagonale Trendlinien — Detection + Retest-Gate (Short/Long)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import SignalDirection
from app.core.time import timeframe_to_timedelta
from app.indicators.structure import SwingPoint, analyze_structure
from app.indicators.trendlines import (
    TrendlineDetectConfig,
    TrendlineGateConfig,
    evaluate_retest_trendline_gate,
    fit_descending_resistance,
    fit_ascending_support,
    fit_falling_resistance,
    fit_rising_support,
)
from app.market_data.types import Candle
from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry
import pandas as pd


def _c(open_time: datetime, high: float, low: float, close: float) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + timeframe_to_timedelta("1h"),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        is_closed=True,
    )


def _lh_series(*, n: int = 48) -> list[Candle]:
    """Drei Lower Highs (~120→115→110), danach flach um 100."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    peaks = {10: 120.0, 20: 115.0, 30: 110.0}
    out: list[Candle] = []
    for i in range(n):
        t = start + timedelta(hours=i)
        if i in peaks:
            px = peaks[i]
            out.append(_c(t, high=px, low=px - 6.0, close=px - 3.0))
        else:
            out.append(_c(t, high=101.0, low=99.0, close=100.0))
    return out


def _hl_series(*, n: int = 48) -> list[Candle]:
    """Drei Higher Lows (~80→85→90), danach flach um 100."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    troughs = {10: 80.0, 20: 85.0, 30: 90.0}
    out: list[Candle] = []
    for i in range(n):
        t = start + timedelta(hours=i)
        if i in troughs:
            px = troughs[i]
            out.append(_c(t, high=px + 6.0, low=px, close=px + 3.0))
        else:
            out.append(_c(t, high=101.0, low=99.0, close=100.0))
    return out


class TestFitDiagonals:
    def test_falling_resistance_min_two_points(self) -> None:
        swings = [
            SwingPoint(index=10, price=120.0, is_high=True),
            SwingPoint(index=20, price=110.0, is_high=True),
        ]
        line = fit_falling_resistance(
            swings,
            eval_idx=25,
            cfg=TrendlineDetectConfig(min_points=2, min_r2=0.0, max_slope_atr=1e9),
        )
        assert line is not None
        assert line.kind == "falling_resistance"
        assert line.is_descending
        assert line.price_at(20) == pytest.approx(110.0, abs=0.01)

    def test_rising_support_min_two_points(self) -> None:
        swings = [
            SwingPoint(index=10, price=80.0, is_high=False),
            SwingPoint(index=20, price=90.0, is_high=False),
        ]
        line = fit_rising_support(
            swings,
            eval_idx=25,
            cfg=TrendlineDetectConfig(min_points=2, min_r2=0.0, max_slope_atr=1e9),
        )
        assert line is not None
        assert line.kind == "rising_support"
        assert line.is_ascending

    def test_compat_aliases(self) -> None:
        swings = [
            SwingPoint(index=10, price=120.0, is_high=True),
            SwingPoint(index=20, price=115.0, is_high=True),
            SwingPoint(index=30, price=110.0, is_high=True),
        ]
        assert fit_descending_resistance(swings, min_points=3) is not None
        lows = [
            SwingPoint(index=10, price=80.0, is_high=False),
            SwingPoint(index=20, price=85.0, is_high=False),
            SwingPoint(index=30, price=90.0, is_high=False),
        ]
        assert fit_ascending_support(lows, min_points=3) is not None


class TestEvaluateGate:
    def test_short_blocks_break_above_line(self) -> None:
        candles = _lh_series()
        fill_idx = 40
        candles[fill_idx] = _c(
            candles[fill_idx].open_time, high=108.0, low=100.0, close=104.0
        )
        result = evaluate_retest_trendline_gate(
            candles,
            fill_idx=fill_idx,
            fill_price=101.5,
            atr=2.0,
            is_long=False,
            cfg=TrendlineGateConfig(
                buffer_atr=0.1,
                detect=TrendlineDetectConfig(min_points=2, min_r2=0.0, max_slope_atr=1e9),
            ),
        )
        assert result.blocked
        assert result.reason == "broke_falling_resistance"

    def test_short_allows_fill_under_line(self) -> None:
        candles = _lh_series()
        fill_idx = 40
        candles[fill_idx] = _c(
            candles[fill_idx].open_time, high=101.5, low=99.5, close=100.5
        )
        result = evaluate_retest_trendline_gate(
            candles,
            fill_idx=fill_idx,
            fill_price=101.2,
            atr=2.0,
            is_long=False,
            cfg=TrendlineGateConfig(
                buffer_atr=0.1,
                detect=TrendlineDetectConfig(min_points=2, min_r2=0.0, max_slope_atr=1e9),
            ),
        )
        assert not result.blocked

    def test_long_blocks_break_below_line(self) -> None:
        candles = _hl_series()
        fill_idx = 40
        candles[fill_idx] = _c(
            candles[fill_idx].open_time, high=100.0, low=92.0, close=96.0
        )
        result = evaluate_retest_trendline_gate(
            candles,
            fill_idx=fill_idx,
            fill_price=98.0,
            atr=2.0,
            is_long=True,
            cfg=TrendlineGateConfig(
                buffer_atr=0.1,
                detect=TrendlineDetectConfig(min_points=2, min_r2=0.0, max_slope_atr=1e9),
            ),
        )
        assert result.blocked
        assert result.reason == "broke_rising_support"


class TestStructureExposesDiagonals:
    def test_falling_resistance_on_lh_series(self) -> None:
        candles = _lh_series(n=45)
        high = pd.Series([c.high for c in candles])
        low = pd.Series([c.low for c in candles])
        close = pd.Series([c.close for c in candles])
        result = analyze_structure(high, low, close, atr_value=3.0, trendline_lookback=40)
        assert result.falling_resistance is not None
        assert result.falling_resistance > float(close.iloc[-1])


class TestArmRetestTrendlineGate:
    def test_short_skips_when_bounce_breaks_resistance(self) -> None:
        candles = _lh_series(n=39)
        arm_time = candles[-1].open_time
        t = arm_time + timedelta(hours=1)
        candles.append(_c(t, high=108.0, low=101.2, close=104.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_SHORT,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=112.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(
                pending_multiplier=4,
                min_bars_in_zone=1,
                trendline_gate_enabled=True,
                trendline_buffer_atr=0.1,
                trendline_min_points=2,
                trendline_min_r2=0.0,
            ),
        )
        assert arm.status == "skipped_trendline_break"
        assert "broke_falling_resistance" in arm.note
        assert not arm.filled

    def test_short_fills_when_gate_disabled(self) -> None:
        candles = _lh_series(n=39)
        arm_time = candles[-1].open_time
        t = arm_time + timedelta(hours=1)
        candles.append(_c(t, high=108.0, low=101.2, close=104.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_SHORT,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=112.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(
                pending_multiplier=4,
                min_bars_in_zone=1,
                trendline_gate_enabled=False,
            ),
        )
        assert arm.filled

    def test_long_skips_when_bounce_breaks_support(self) -> None:
        candles = _hl_series(n=39)
        arm_time = candles[-1].open_time
        t = arm_time + timedelta(hours=1)
        candles.append(_c(t, high=98.8, low=92.0, close=96.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=88.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(
                pending_multiplier=4,
                min_bars_in_zone=1,
                trendline_gate_enabled=True,
                trendline_buffer_atr=0.1,
                trendline_min_points=2,
                trendline_min_r2=0.0,
            ),
        )
        assert arm.status == "skipped_trendline_break"
        assert "broke_rising_support" in arm.note
        assert not arm.filled
