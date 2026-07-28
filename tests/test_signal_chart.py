"""Tests fuer Signal-Charts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.charts.signal_chart import build_signal_chart
from app.core.enums import SignalDirection
from app.market_data.types import Candle, CandleSeries
from app.services.analysis_service import AnalysisOutcome
from tests.test_dedup import make_result


def _make_candles(count: int = 80, *, start: float = 100.0) -> CandleSeries:
    candles: list[Candle] = []
    start_time = datetime(2026, 1, 1, tzinfo=UTC)
    price = start
    for i in range(count):
        open_time = start_time + timedelta(hours=i)
        close_time = open_time + timedelta(hours=1)
        open_price = price
        close_price = price + (1 if i % 3 else -0.5)
        high = max(open_price, close_price) + 0.8
        low = min(open_price, close_price) - 0.8
        candles.append(
            Candle(
                open_time=open_time,
                close_time=close_time,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=1000.0,
            )
        )
        price = close_price
    return CandleSeries(symbol="BTCUSDT", timeframe="1h", candles=candles)


class TestSignalChart:
    def test_builds_png_for_actionable_signal(self) -> None:
        result = make_result(direction=SignalDirection.LONG)
        outcome = AnalysisOutcome(
            result=result,
            price_precision=2,
            chart_series=_make_candles(),
        )
        png = build_signal_chart(outcome)
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_returns_none_without_risk(self) -> None:
        result = make_result(direction=SignalDirection.NEUTRAL)
        result.risk = None
        outcome = AnalysisOutcome(
            result=result,
            price_precision=2,
            chart_series=_make_candles(),
        )
        assert build_signal_chart(outcome) is None

    def test_returns_none_without_candles(self) -> None:
        result = make_result(direction=SignalDirection.LONG)
        outcome = AnalysisOutcome(result=result, price_precision=2, chart_series=None)
        assert build_signal_chart(outcome) is None
