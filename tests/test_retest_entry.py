"""Unit tests for ATR retest / pullback entry thesis (arm B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import SignalDirection
from app.core.time import timeframe_to_timedelta
from app.market_data.types import Candle
from app.signals.retest_entry import (
    RetestEntryConfig,
    arm_retest_entry,
    zone_overlaps_stop,
)
from decimal import Decimal


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


def _history(n: int = 40, *, base: float = 100.0) -> list[Candle]:
    """Flat-ish history so Wilder ATR is stable (~2)."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(n):
        t = start + timedelta(hours=i)
        out.append(_c(t, high=base + 1.0, low=base - 1.0, close=base))
    return out


class TestArmRetestEntry:
    def test_long_fills_on_pullback_into_zone(self) -> None:
        # Truncate at signal bar so only appended bars can fill.
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        # ATR ~2 → zone [98, 99.3] for long at ref=100
        pull_t = arm_time + timedelta(hours=1)
        candles.append(_c(pull_t, high=100.0, low=98.5, close=99.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.filled
        assert arm.fill_price is not None
        assert 98.0 <= arm.fill_price <= 99.3
        assert arm.stop is not None
        assert arm.stop < arm.fill_price
        # Original R=5 preserved
        assert arm.fill_price - arm.stop == pytest.approx(5.0)

    def test_skips_when_sl_hit_before_retest(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        sl_t = arm_time + timedelta(hours=1)
        # Stay above zone far edge (98) until SL — use a gap through zone to SL.
        # low=94 hits SL=95; also overlaps zone, but SL check wins first.
        candles.append(_c(sl_t, high=100.0, low=94.0, close=96.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.status == "skipped_sl"

    def test_pending_when_no_touch_yet(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        nxt = arm_time + timedelta(hours=1)
        # Stay above zone far edge (~99.3)
        candles.append(_c(nxt, high=102.0, low=100.5, close=101.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.status == "pending"

    def test_short_fills_on_pullback_into_zone(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        # ATR ~2 → short zone [100.7, 102]
        pull_t = arm_time + timedelta(hours=1)
        candles.append(_c(pull_t, high=101.5, low=100.0, close=101.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_SHORT,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=105.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.filled
        assert arm.fill_price is not None
        assert 100.7 <= arm.fill_price <= 102.0
        assert arm.stop is not None
        assert arm.stop > arm.fill_price
        assert arm.stop - arm.fill_price == pytest.approx(5.0)

    def test_skips_on_pending_expiry(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        # 5 bars after arm with no zone touch → expiry at 4×1h
        for i in range(1, 6):
            t = arm_time + timedelta(hours=i)
            candles.append(_c(t, high=102.0, low=100.5, close=101.0))

        arm = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.status == "skipped_expiry"

    def test_fills_on_first_bar_in_zone_by_default(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        t1 = arm_time + timedelta(hours=1)
        candles.append(_c(t1, high=100.0, low=98.5, close=99.0))
        arm_one = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
        )
        assert arm_one.filled
        assert arm_one.bars_waited == 1

    def test_requires_two_bars_when_configured(self) -> None:
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        t1 = arm_time + timedelta(hours=1)
        candles.append(_c(t1, high=100.0, low=98.5, close=99.0))
        arm_one = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(min_bars_in_zone=2),
        )
        assert arm_one.status == "pending"

        t2 = arm_time + timedelta(hours=2)
        candles.append(_c(t2, high=100.0, low=98.4, close=98.8))
        arm_two = arm_retest_entry(
            direction=SignalDirection.STRONG_LONG,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=95.0,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(min_bars_in_zone=2),
        )
        assert arm_two.filled


class TestZoneStopOverlap:
    def test_detects_stop_inside_zone(self) -> None:
        assert zone_overlaps_stop(Decimal("1.549"), Decimal("1.554"), Decimal("1.552"))
        assert not zone_overlaps_stop(Decimal("1.549"), Decimal("1.554"), Decimal("1.560"))

    def test_inclusive_edges(self) -> None:
        assert zone_overlaps_stop(Decimal("10"), Decimal("12"), Decimal("10"))
        assert zone_overlaps_stop(Decimal("10"), Decimal("12"), Decimal("12"))

    def test_arm_skips_when_stop_inside_retest_zone(self) -> None:
        # ATR~2, short ref=100 → zone ~[101.1, 102.0]; stop clearly inside.
        candles = _history(21, base=100.0)
        arm_time = candles[-1].open_time
        arm = arm_retest_entry(
            direction=SignalDirection.SHORT,
            arm_time=arm_time,
            reference_entry=100.0,
            original_stop=101.5,
            timeframe="1h",
            candles=candles,
            config=RetestEntryConfig(pending_multiplier=4, min_bars_in_zone=1),
        )
        assert arm.status == "skipped_zone_stop_overlap"
        assert not arm.filled
        assert arm.resolved_at == arm_time
