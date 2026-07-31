"""Tests fuer die professionelle Signal-Report-Card."""

from __future__ import annotations

from app.charts.signal_report_card import build_signal_report_card, compose_signal_report
from app.core.enums import SignalDirection
from app.services.analysis_service import AnalysisOutcome
from tests.test_dedup import make_result
from tests.test_signal_chart import _make_candles


def test_builds_signal_report_card_png() -> None:
    result = make_result(direction=SignalDirection.STRONG_SHORT, score=84.0)
    result.reasons = ["ADX bestaetigt Trend", "EMA-Stack short", "Volumen steigt"]
    result.counter_arguments = ["Kurzfristiger Squeeze moeglich"]
    outcome = AnalysisOutcome(
        result=result,
        price_precision=2,
        chart_series=_make_candles(),
    )
    png = build_signal_report_card(outcome)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_compose_stacks_report_and_chart() -> None:
    from app.charts.signal_chart import build_signal_chart

    result = make_result(direction=SignalDirection.LONG, score=80.0)
    outcome = AnalysisOutcome(
        result=result,
        price_precision=2,
        chart_series=_make_candles(),
    )
    report = build_signal_report_card(outcome)
    chart = build_signal_chart(outcome)
    composed = compose_signal_report(report, chart)
    assert composed is not None
    assert composed[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(composed) > len(report or b"")
