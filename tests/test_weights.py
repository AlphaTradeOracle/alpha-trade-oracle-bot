"""Tests der Gewichtung. Die Summe 1.0 ist eine harte Invariante."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.enums import ScoreCategory
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights


class TestWeightSum:
    def test_default_weights_sum_to_one(self) -> None:
        assert DEFAULT_WEIGHTS.total() == pytest.approx(1.0)

    def test_all_eight_categories_are_covered(self) -> None:
        assert set(DEFAULT_WEIGHTS.as_dict()) == set(ScoreCategory)

    def test_default_weights_match_v1_structure_mtf(self) -> None:
        """Default = v1: Structure 16.38%, MTF 10.46% (18/18 reverted)."""
        assert DEFAULT_WEIGHTS.trend == pytest.approx(0.2730)
        assert DEFAULT_WEIGHTS.momentum == pytest.approx(0.2184)
        assert DEFAULT_WEIGHTS.volume == pytest.approx(0.1638)
        assert DEFAULT_WEIGHTS.market_structure == pytest.approx(0.1638)
        assert DEFAULT_WEIGHTS.multi_timeframe == pytest.approx(0.1046)
        assert DEFAULT_WEIGHTS.volatility == pytest.approx(0.0437)
        assert DEFAULT_WEIGHTS.risk_reward == pytest.approx(0.0327)
        assert DEFAULT_WEIGHTS.sentiment == pytest.approx(0.0)

    def test_rejects_weights_that_do_not_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match=r"1\.0"):
            StrategyWeights(trend=0.5, momentum=0.5, volume=0.5)

    def test_accepts_custom_weights_summing_to_one(self) -> None:
        weights = StrategyWeights(
            trend=0.30,
            momentum=0.20,
            volume=0.10,
            market_structure=0.15,
            multi_timeframe=0.15,
            volatility=0.04,
            sentiment=0.03,
            risk_reward=0.03,
        )
        assert weights.total() == pytest.approx(1.0)
        assert weights.trend == pytest.approx(0.30)

    def test_rejects_negative_weight(self) -> None:
        with pytest.raises(ValidationError):
            StrategyWeights(trend=-0.1)


class TestSentimentRedistribution:
    def test_without_sentiment_still_sums_to_one(self) -> None:
        redistributed = DEFAULT_WEIGHTS.without_sentiment()
        assert redistributed.sentiment == pytest.approx(0.0)
        assert redistributed.total() == pytest.approx(1.0)

    def test_redistribution_preserves_relative_order(self) -> None:
        redistributed = DEFAULT_WEIGHTS.without_sentiment()
        assert redistributed.trend > redistributed.momentum > redistributed.volume

    def test_redistribution_is_idempotent(self) -> None:
        once = DEFAULT_WEIGHTS.without_sentiment()
        twice = once.without_sentiment()
        assert once.as_dict() == twice.as_dict()


class TestImmutability:
    def test_weights_are_frozen(self) -> None:
        """Eine geaenderte Gewichtung muss eine neue Strategieversion sein."""
        with pytest.raises(ValidationError):
            DEFAULT_WEIGHTS.trend = 0.5  # type: ignore[misc]
