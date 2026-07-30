"""Tests fuer Signal-Charts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.charts.signal_chart import (
    build_paper_trade_chart,
    build_signal_chart,
    resolve_paper_chart_timeframe,
)
from app.core.config import Settings
from app.core.enums import SignalDirection
from app.market_data.types import Candle, CandleSeries
from app.models.paper import PaperPosition
from app.services.analysis_service import AnalysisOutcome
from tests.test_dedup import make_result


def _make_candles(count: int = 80, *, start: float = 100.0, timeframe: str = "1h") -> CandleSeries:
    candles: list[Candle] = []
    start_time = datetime(2026, 1, 1, tzinfo=UTC)
    price = start
    delta = timedelta(hours=1) if timeframe == "1h" else timedelta(hours=4)
    for i in range(count):
        open_time = start_time + delta * i
        close_time = open_time + delta
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
    return CandleSeries(symbol="BTCUSDT", timeframe=timeframe, candles=candles)


def _sample_position(**overrides) -> PaperPosition:
    defaults = {
        "id": 1,
        "account_id": 1,
        "symbol": "BTCUSDT",
        "direction": SignalDirection.LONG.value,
        "status": "open",
        "timeframe": "1h",
        "entry_price": Decimal("100000"),
        "stop_loss": Decimal("98000"),
        "current_stop": Decimal("98000"),
        "take_profit_1": Decimal("103000"),
        "take_profit_2": Decimal("106000"),
        "take_profit_3": Decimal("110000"),
        "initial_quantity": Decimal("0.01"),
        "remaining_quantity": Decimal("0.01"),
        "margin_used": Decimal("100"),
        "notional": Decimal("1000"),
        "leverage": 10.0,
        "fees": Decimal("1"),
        "signal_score": 82.0,
        "opened_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return PaperPosition(**defaults)


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


class TestPaperTradeChart:
    def test_builds_png_for_open_position(self) -> None:
        position = _sample_position()
        series = _make_candles(timeframe="4h")
        png = build_paper_trade_chart(position, series, price_precision=2)
        assert png is not None
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_resolve_paper_chart_timeframe_bumps_primary(self) -> None:
        settings = Settings(
            default_timeframes="15m,1h,4h,1d",
            primary_timeframe="1h",
        )
        assert resolve_paper_chart_timeframe("1h", settings) == "4h"

    def test_resolve_paper_chart_timeframe_respects_override(self) -> None:
        settings = Settings(
            default_timeframes="15m,1h,4h,1d",
            paper_telegram_chart_timeframe="1d",
        )
        assert resolve_paper_chart_timeframe("1h", settings) == "1d"
