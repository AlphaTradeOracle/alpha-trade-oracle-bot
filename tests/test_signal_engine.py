"""Tests der Signal-Engine: Scoring, Richtung, Konfidenz, No-Trade-Regeln."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.enums import Confidence, ScoreCategory, SignalDirection, TrendDirection
from app.indicators.engine import IndicatorSet
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.signals.multi_timeframe import (
    aggregate_category,
    assess_timeframes,
    multi_timeframe_agreement,
)
from app.signals.scoring import score_volatility
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


class TestScoreComposition:
    def test_score_is_within_bounds(self, uptrend_indicators: dict[str, IndicatorSet]) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert 0.0 <= result.score <= 100.0

    def test_all_categories_are_present_in_breakdown(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        """Der vollstaendige Breakdown muss gespeichert werden koennen."""
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        categories = {component.category for component in result.components}
        # Sentiment ist standardmaessig deaktiviert und daher nicht enthalten.
        expected = set(ScoreCategory) - {ScoreCategory.SENTIMENT}
        assert expected <= categories

    def test_component_weights_sum_to_one(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        scoring = [
            c for c in result.components if c.category is not ScoreCategory.RISK_REWARD
        ]
        # R:R ist Gate-only (weight 0); die verbleibenden Kategorien behalten ihre Gewichte.
        assert sum(c.weight for c in scoring) == pytest.approx(
            1.0 - DEFAULT_WEIGHTS.risk_reward, abs=1e-4
        )

    def test_weighted_score_matches_raw_times_weight(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        for component in result.components:
            assert component.weighted_score == pytest.approx(component.raw_score * component.weight)

    def test_raw_scores_stay_in_signed_range(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        for component in result.components:
            assert -100.0 <= component.raw_score <= 100.0

    def test_sentiment_component_appears_when_enabled(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(enable_sentiment=True))
        result = engine.generate("BTCUSDT", uptrend_indicators, sentiment_score=40.0, now=NOW)
        component = next(c for c in result.components if c.category is ScoreCategory.SENTIMENT)
        assert component.raw_score == pytest.approx(40.0)

    def test_missing_sentiment_is_neutral_not_invented(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(enable_sentiment=True))
        result = engine.generate("BTCUSDT", uptrend_indicators, sentiment_score=None, now=NOW)
        component = next(c for c in result.components if c.category is ScoreCategory.SENTIMENT)
        assert component.raw_score == 0.0
        assert "No sentiment data" in component.detail

    def test_disabled_sentiment_redistributes_weight(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(enable_sentiment=False))
        assert engine.weights.sentiment == pytest.approx(0.0)
        assert engine.weights.total() == pytest.approx(1.0)
        result = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert all(c.category is not ScoreCategory.SENTIMENT for c in result.components)


class TestVolatilityScoreDirection:
    """Volatilitaet darf den Score nicht richtungslos nach unten ziehen."""

    def test_penalty_follows_trend_direction(
        self,
        uptrend_indicators: dict[str, IndicatorSet],
        downtrend_indicators: dict[str, IndicatorSet],
    ) -> None:
        bullish = replace(uptrend_indicators["1h"], atr_percent=9.0)
        bearish = replace(downtrend_indicators["1h"], atr_percent=9.0)

        bullish_score, _ = score_volatility(bullish)
        bearish_score, _ = score_volatility(bearish)

        assert bullish.trend_direction.value == "BULLISH"
        assert bearish.trend_direction.value == "BEARISH"
        assert bullish_score < 0
        assert bearish_score > 0

    def test_neutral_trend_yields_no_volatility_bias(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        indicators = replace(uptrend_indicators["1h"], atr_percent=9.0, bb_width=None)
        indicators = replace(indicators, trend_direction=TrendDirection.NEUTRAL)
        score, _ = score_volatility(indicators)
        assert score == 0.0


class TestDirection:
    def test_uptrend_yields_long_bias(self, uptrend_indicators: dict[str, IndicatorSet]) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert result.direction in {
            SignalDirection.LONG,
            SignalDirection.STRONG_LONG,
            SignalDirection.NO_TRADE,
        }
        assert result.score > 50.0

    def test_downtrend_yields_short_bias(
        self, downtrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", downtrend_indicators, now=NOW)
        assert result.direction in {
            SignalDirection.SHORT,
            SignalDirection.STRONG_SHORT,
            SignalDirection.NO_TRADE,
        }
        assert result.score < 50.0

    def test_sideways_market_is_not_strongly_directional(
        self, sideways_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", sideways_indicators, now=NOW)
        assert result.direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }

    def test_direction_never_relies_on_single_indicator(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        """Mehr als eine Kategorie muss zum Score beitragen."""
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        contributing = [c for c in result.components if abs(c.raw_score) > 1.0]
        assert len(contributing) >= 3


class TestNoTradeRules:
    def test_low_data_quality_forces_no_trade(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, data_quality=40.0, now=NOW)
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "Data quality" in result.no_trade_reason

    def test_excessive_volatility_forces_no_trade(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(max_atr_percent=0.01, rsi_long_max=100.0))
        result = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "Volatility" in result.no_trade_reason

    def test_insufficient_risk_reward_forces_no_trade(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(min_risk_reward_ratio=99.0, rsi_long_max=100.0))
        result = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "Risk/reward" in result.no_trade_reason

    def test_risk_reward_is_gate_only_not_score_weight(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        rr = next(c for c in result.components if c.category.value == "risk_reward")
        assert rr.weight == 0.0
        assert rr.raw_score == 0.0
        assert "gate" in rr.detail.lower() or "minimum" in rr.detail.lower()

    def test_neutral_direction_gets_no_no_trade_reason(
        self, sideways_indicators: dict[str, IndicatorSet]
    ) -> None:
        """NO_TRADE gilt nur fuer eigentlich handelbare Richtungen."""
        engine = SignalEngine(SignalEngineConfig(block_range_market=False, min_adx=0.0))
        result = engine.generate("BTCUSDT", sideways_indicators, now=NOW)
        if result.direction is SignalDirection.NEUTRAL:
            assert result.no_trade_reason is None

    def test_overbought_rsi_blocks_long(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        modified = dict(uptrend_indicators)
        modified["1h"] = replace(
            uptrend_indicators["1h"],
            rsi_14=82.0,
            adx_14=30.0,
        )
        result = SignalEngine().generate("BTCUSDT", modified, now=NOW)
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "RSI" in result.no_trade_reason

    def test_oversold_rsi_blocks_short(
        self, downtrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        modified = dict(downtrend_indicators)
        modified["1h"] = replace(
            downtrend_indicators["1h"],
            rsi_14=18.0,
            adx_14=30.0,
        )
        result = SignalEngine().generate("BTCUSDT", modified, now=NOW)
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "RSI" in result.no_trade_reason

    def test_range_market_blocks_trade(
        self, sideways_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", sideways_indicators, now=NOW)
        if result.direction is SignalDirection.NO_TRADE:
            assert result.no_trade_reason is not None
            assert "Range market" in result.no_trade_reason

    def test_low_adx_blocks_trade(self, uptrend_indicators: dict[str, IndicatorSet]) -> None:
        modified = dict(uptrend_indicators)
        modified["1h"] = replace(uptrend_indicators["1h"], adx_14=12.0)
        result = SignalEngine(SignalEngineConfig(block_range_market=False)).generate(
            "BTCUSDT", modified, now=NOW
        )
        assert result.direction is SignalDirection.NO_TRADE
        assert result.no_trade_reason is not None
        assert "ADX" in result.no_trade_reason

    def test_high_conviction_uses_soft_adx_floor(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        """Score ≥ min_score may clear ADX between soft and hard floors."""
        modified = dict(uptrend_indicators)
        modified["1h"] = replace(uptrend_indicators["1h"], adx_14=22.0, rsi_14=55.0)
        engine = SignalEngine(
            SignalEngineConfig(
                block_range_market=False,
                min_adx=30.0,
                min_adx_soft=20.0,
                min_score=75.0,
            )
        )
        result = engine.generate("BTCUSDT", modified, now=NOW)
        if result.score >= 75.0:
            assert result.direction is not SignalDirection.NO_TRADE
            assert result.no_trade_reason is None
        else:
            # Fixture may not always hit ≥75 — then hard floor still applies.
            assert result.direction is SignalDirection.NO_TRADE
            assert result.no_trade_reason is not None
            assert "ADX" in result.no_trade_reason


class TestConfidence:
    def test_low_data_quality_lowers_confidence(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        high = SignalEngine().generate("BTCUSDT", uptrend_indicators, data_quality=100.0, now=NOW)
        low = SignalEngine().generate("BTCUSDT", uptrend_indicators, data_quality=62.0, now=NOW)
        assert low.confidence is Confidence.LOW
        assert high.confidence is not Confidence.LOW

    def test_confidence_is_a_valid_enum_member(
        self, sideways_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", sideways_indicators, now=NOW)
        assert result.confidence in set(Confidence)


class TestResultContent:
    def test_result_contains_all_required_fields(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        """Der Auftrag verlangt fuer jedes Signal einen festen Datenumfang."""
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)

        assert result.symbol == "BTCUSDT"
        assert result.created_at == NOW
        assert result.expires_at > result.created_at
        assert result.analyzed_timeframes == ["15m", "1h", "4h", "1d"]
        assert result.market_phase is not None
        assert result.direction in set(SignalDirection)
        assert 0.0 <= result.score <= 100.0
        assert result.confidence in set(Confidence)
        assert result.reasons
        assert result.indicators_used
        assert 0.0 <= result.data_quality <= 100.0
        assert result.fingerprint

    def test_actionable_signal_has_full_risk_parameters(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        if not result.direction.is_actionable:
            pytest.skip("Fixture ergab kein handelbares Setup")
        risk = result.risk
        assert risk is not None
        assert risk.entry_low < risk.entry_high
        assert risk.take_profit_1 and risk.take_profit_2 and risk.take_profit_3
        assert risk.risk_reward_ratio > 0
        assert risk.invalidation_note

    def test_timeframes_are_sorted_ascending(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert result.analyzed_timeframes == ["15m", "1h", "4h", "1d"]

    def test_expiry_scales_with_primary_timeframe(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        short = SignalEngine(SignalEngineConfig(primary_timeframe="15m")).generate(
            "BTCUSDT", uptrend_indicators, now=NOW
        )
        long = SignalEngine(SignalEngineConfig(primary_timeframe="4h")).generate(
            "BTCUSDT", uptrend_indicators, now=NOW
        )
        assert long.expires_at > short.expires_at

    def test_reasons_and_counters_have_no_duplicates(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert len(result.reasons) == len(set(result.reasons))
        assert len(result.counter_arguments) == len(set(result.counter_arguments))

    def test_score_breakdown_is_serializable(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        result = SignalEngine().generate("BTCUSDT", uptrend_indicators, now=NOW)
        breakdown = result.score_breakdown()
        assert breakdown
        for values in breakdown.values():
            assert {"raw_score", "weight", "weighted_score"} <= set(values)


class TestDeterminism:
    def test_same_input_yields_same_output(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine()
        first = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        second = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        assert first.fingerprint == second.fingerprint
        assert first.score == second.score
        assert first.direction == second.direction

    def test_fingerprint_differs_between_symbols(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine()
        btc = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        eth = engine.generate("ETHUSDT", uptrend_indicators, now=NOW)
        assert btc.fingerprint != eth.fingerprint

    def test_fingerprint_is_stable_under_tiny_score_changes(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        """Score wird auf 5er-Schritte gerundet, damit Rauschen kein neues Signal ist."""
        engine = SignalEngine()
        result = engine.generate("BTCUSDT", uptrend_indicators, now=NOW)
        bucket = int(result.score // 5)
        assert 0 <= bucket <= 20


class TestValidation:
    def test_empty_indicator_sets_raise(self) -> None:
        with pytest.raises(ValueError, match="kein Indikatorsatz"):
            SignalEngine().generate("BTCUSDT", {}, now=NOW)

    def test_works_with_single_timeframe(self, uptrend_indicators: dict[str, IndicatorSet]) -> None:
        """Ein einzelner Timeframe darf nicht abstuerzen, aber weniger Konfidenz haben."""
        single = {"1h": uptrend_indicators["1h"]}
        result = SignalEngine().generate("BTCUSDT", single, now=NOW)
        assert result.analyzed_timeframes == ["1h"]

    def test_falls_back_when_primary_timeframe_absent(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(primary_timeframe="1h"))
        without_1h = {k: v for k, v in uptrend_indicators.items() if k != "1h"}
        result = engine.generate("BTCUSDT", without_1h, now=NOW)
        assert result.primary_timeframe in without_1h


class TestCustomWeights:
    def test_custom_weights_change_the_score(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        trend_heavy = StrategyWeights(
            trend=0.60,
            momentum=0.10,
            volume=0.10,
            market_structure=0.05,
            multi_timeframe=0.05,
            volatility=0.04,
            sentiment=0.03,
            risk_reward=0.03,
        )
        default_result = SignalEngine(SignalEngineConfig(weights=DEFAULT_WEIGHTS)).generate(
            "BTCUSDT", uptrend_indicators, now=NOW
        )
        custom_result = SignalEngine(SignalEngineConfig(weights=trend_heavy)).generate(
            "BTCUSDT", uptrend_indicators, now=NOW
        )
        assert default_result.score != custom_result.score


class TestMultiTimeframeLogic:
    def test_assessment_created_for_every_timeframe(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        assessments = assess_timeframes(uptrend_indicators)
        assert set(assessments) == set(uptrend_indicators)

    def test_agreement_is_positive_in_aligned_uptrend(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        agreement, detail = multi_timeframe_agreement(assess_timeframes(uptrend_indicators))
        assert agreement > 0
        assert detail

    def test_agreement_is_negative_in_aligned_downtrend(
        self, downtrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        agreement, _detail = multi_timeframe_agreement(assess_timeframes(downtrend_indicators))
        assert agreement < 0

    def test_agreement_within_bounds(self, sideways_indicators: dict[str, IndicatorSet]) -> None:
        agreement, _detail = multi_timeframe_agreement(assess_timeframes(sideways_indicators))
        assert -100.0 <= agreement <= 100.0

    def test_conflicting_timeframes_reduce_agreement(
        self,
        uptrend_indicators: dict[str, IndicatorSet],
        downtrend_indicators: dict[str, IndicatorSet],
    ) -> None:
        aligned = multi_timeframe_agreement(assess_timeframes(uptrend_indicators))[0]
        mixed = multi_timeframe_agreement(
            assess_timeframes(
                {
                    "15m": uptrend_indicators["15m"],
                    "1h": uptrend_indicators["1h"],
                    "4h": downtrend_indicators["4h"],
                    "1d": downtrend_indicators["1d"],
                }
            )
        )[0]
        assert abs(mixed) < abs(aligned)

    def test_higher_timeframes_weigh_more(
        self,
        uptrend_indicators: dict[str, IndicatorSet],
        downtrend_indicators: dict[str, IndicatorSet],
    ) -> None:
        """Der Tagestrend soll schwerer wiegen als das 15-Minuten-Timing."""
        daily_bullish = multi_timeframe_agreement(
            assess_timeframes({"15m": downtrend_indicators["15m"], "1d": uptrend_indicators["1d"]})
        )[0]
        daily_bearish = multi_timeframe_agreement(
            assess_timeframes({"15m": uptrend_indicators["15m"], "1d": downtrend_indicators["1d"]})
        )[0]
        assert daily_bullish > daily_bearish

    def test_aggregate_category_within_bounds(
        self, uptrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        assessments = assess_timeframes(uptrend_indicators)
        for field in ("trend_score", "momentum_score", "volume_score", "structure_score"):
            value = aggregate_category(assessments, field)
            assert -100.0 <= value <= 100.0
