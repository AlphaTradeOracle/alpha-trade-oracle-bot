"""Unit tests for WUSDT-lesson post-signal skip rules."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums import Confidence, MarketPhase, SignalDirection, StructureState
from app.indicators.engine import IndicatorSet
from app.indicators.structure import StructureAnalysis
from app.signals.lesson_filters import (
    COMBO_CORE,
    LESSON_RULE_BULLISH_DIV,
    LESSON_RULE_RSI_RISING,
    lesson_skip_reason,
)
from app.signals.types import SignalResult, TimeframeAssessment


def _signal(
    *,
    direction: SignalDirection = SignalDirection.SHORT,
    bullish_div: bool = False,
    rsi: float | None = 40.0,
    rsi_prev: float | None = 35.0,
    volume_ratio: float | None = 1.2,
    bb_width: float | None = 1.0,
    bb_avg: float | None = 1.0,
    breakout_down: bool = False,
    counters: list[str] | None = None,
) -> SignalResult:
    indicators = IndicatorSet(
        timeframe="1h",
        candle_open_time=datetime.now(timezone.utc),
        close_price=1.0,
        rsi_14=rsi,
        rsi_previous=rsi_prev,
        volume_ratio=volume_ratio,
        bb_width=bb_width,
        bb_width_average=bb_avg,
        structure=StructureAnalysis(
            state=StructureState.LH_LL,
            bullish_divergence=bullish_div,
            breakout_down=breakout_down,
        ),
    )
    assessment = TimeframeAssessment(
        timeframe="1h",
        role_weight=1.0,
        indicators=indicators,
        trend_score=-50.0,
        momentum_score=-40.0,
        volume_score=-20.0,
        volatility_score=-10.0,
        structure_score=-30.0,
    )
    return SignalResult(
        symbol="TESTUSDT",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        direction=direction,
        score=22.0,
        confidence=Confidence.HIGH,
        market_phase=MarketPhase.DOWNTREND,
        primary_timeframe="1h",
        analyzed_timeframes=["1h"],
        reference_price=1.0,
        data_quality=100.0,
        components=[],
        assessments={"1h": assessment},
        risk=None,
        counter_arguments=counters or [],
    )


def test_old_rules_take_everything() -> None:
    signal = _signal(bullish_div=True, rsi=45, rsi_prev=30)
    assert lesson_skip_reason(signal, ()) is None


def test_bullish_div_skips_short() -> None:
    signal = _signal(bullish_div=True)
    assert lesson_skip_reason(signal, (LESSON_RULE_BULLISH_DIV,)) == "short_bullish_divergence"


def test_bullish_div_does_not_skip_long() -> None:
    signal = _signal(direction=SignalDirection.LONG, bullish_div=True)
    assert lesson_skip_reason(signal, (LESSON_RULE_BULLISH_DIV,)) is None


def test_rsi_rising_skips_short() -> None:
    signal = _signal(rsi=42, rsi_prev=35)
    assert lesson_skip_reason(signal, (LESSON_RULE_RSI_RISING,)) == "short_rsi_rising"


def test_combo_core_hits_divergence_first() -> None:
    signal = _signal(bullish_div=True, rsi=42, rsi_prev=35, breakout_down=True, volume_ratio=0.4)
    assert lesson_skip_reason(signal, COMBO_CORE) == "short_bullish_divergence"
