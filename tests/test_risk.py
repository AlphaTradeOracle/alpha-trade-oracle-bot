"""Tests des Risikomanagements: Stop-Loss, Take-Profit, R:R, Positionsgroesse."""

from __future__ import annotations

import pytest

from app.core.enums import SignalDirection
from app.indicators.engine import IndicatorSet
from app.indicators.structure import StructureAnalysis
from app.signals.risk import RiskConfig, RiskManager


def make_indicators(
    *,
    close_price: float = 40_000.0,
    atr_14: float | None = 400.0,
    supports: list[float] | None = None,
    resistances: list[float] | None = None,
) -> IndicatorSet:
    """Minimaler Indikatorsatz — der RiskManager braucht nur Kurs, ATR und Struktur."""
    structure = StructureAnalysis(
        supports=supports or [],
        resistances=resistances or [],
        nearest_support=max(supports) if supports else None,
        nearest_resistance=min(resistances) if resistances else None,
    )
    from datetime import UTC, datetime

    return IndicatorSet(
        timeframe="1h",
        candle_open_time=datetime(2024, 1, 1, tzinfo=UTC),
        close_price=close_price,
        atr_14=atr_14,
        atr_percent=(atr_14 / close_price * 100.0) if atr_14 and close_price else None,
        structure=structure,
    )


class TestStopLoss:
    def test_long_stop_is_below_entry(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        assert risk.stop_loss < risk.entry_low

    def test_short_stop_is_above_entry(self) -> None:
        risk = RiskManager().calculate(SignalDirection.SHORT, make_indicators())
        assert risk is not None
        assert risk.stop_loss > risk.entry_high

    def test_stop_distance_scales_with_atr(self) -> None:
        narrow = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=200.0))
        wide = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=800.0))
        assert narrow is not None and wide is not None
        assert wide.stop_distance_percent > narrow.stop_distance_percent

    def test_stop_distance_follows_atr_multiplier(self) -> None:
        manager = RiskManager(RiskConfig(atr_multiplier=2.0))
        risk = manager.calculate(SignalDirection.LONG, make_indicators(atr_14=400.0))
        assert risk is not None
        expected = risk.entry_low - 400.0 * 2.0
        assert risk.stop_loss == pytest.approx(expected)

    def test_stop_is_placed_below_support_lying_just_under_atr_stop(self) -> None:
        """Ein Stop knapp ueber einem Support wird ueberproportional abgeraeumt.

        Support bei 39.200 liegt knapp unter dem ATR-Stop (39.300). Ohne
        Korrektur wuerde der Kurs beim Anlaufen des Supports zuerst den Stop
        abraeumen.
        """
        indicators = make_indicators(close_price=40_000.0, atr_14=400.0, supports=[39_200.0])
        risk = RiskManager().calculate(SignalDirection.LONG, indicators)
        assert risk is not None
        assert risk.stop_loss < 39_200.0
        assert any("Support" in warning for warning in risk.warnings)

    def test_stop_is_placed_above_resistance_for_short(self) -> None:
        indicators = make_indicators(close_price=40_000.0, atr_14=400.0, resistances=[40_800.0])
        risk = RiskManager().calculate(SignalDirection.SHORT, indicators)
        assert risk is not None
        assert risk.stop_loss > 40_800.0
        assert any("Widerstand" in warning for warning in risk.warnings)

    def test_support_above_atr_stop_leaves_stop_untouched(self) -> None:
        """Liegt der Support ueber dem ATR-Stop, ist der Stop bereits sicher."""
        indicators = make_indicators(close_price=40_000.0, atr_14=400.0, supports=[39_800.0])
        risk = RiskManager().calculate(SignalDirection.LONG, indicators)
        assert risk is not None
        assert risk.stop_loss == pytest.approx(risk.entry_low - 400.0 * 1.5)

    def test_distant_support_is_ignored(self) -> None:
        """Ein weit entfernter Support darf den ATR-Stop nicht verschieben."""
        indicators = make_indicators(close_price=40_000.0, atr_14=400.0, supports=[30_000.0])
        risk = RiskManager().calculate(SignalDirection.LONG, indicators)
        assert risk is not None
        assert risk.stop_loss == pytest.approx(risk.entry_low - 400.0 * 1.5)

    def test_too_tight_stop_is_widened(self) -> None:
        manager = RiskManager(RiskConfig(min_stop_distance_percent=1.0))
        risk = manager.calculate(SignalDirection.LONG, make_indicators(atr_14=1.0))
        assert risk is not None
        assert risk.stop_distance_percent == pytest.approx(1.0)
        assert any("aufgeweitet" in warning for warning in risk.warnings)

    def test_too_wide_stop_is_flagged_but_not_moved(self) -> None:
        """Ein kuenstlich enger Stop waere in einem volatilen Markt gefaehrlicher."""
        manager = RiskManager(RiskConfig(max_stop_distance_percent=2.0))
        risk = manager.calculate(SignalDirection.LONG, make_indicators(atr_14=2_000.0))
        assert risk is not None
        assert risk.stop_distance_percent > 2.0
        assert any("ungewoehnlich weit" in warning for warning in risk.warnings)


class TestTakeProfit:
    def test_long_targets_are_strictly_ascending(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        assert risk.entry_high < risk.take_profit_1 < risk.take_profit_2 < risk.take_profit_3

    def test_short_targets_are_strictly_descending(self) -> None:
        risk = RiskManager().calculate(SignalDirection.SHORT, make_indicators())
        assert risk is not None
        assert risk.entry_low > risk.take_profit_1 > risk.take_profit_2 > risk.take_profit_3

    def test_targets_are_multiples_of_risk(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        distance = risk.entry_low - risk.stop_loss
        assert risk.take_profit_1 == pytest.approx(risk.entry_low + distance * 1.0)
        assert risk.take_profit_2 == pytest.approx(risk.entry_low + distance * 2.0)
        assert risk.take_profit_3 == pytest.approx(risk.entry_low + distance * 3.0)

    def test_target_is_pulled_below_blocking_resistance(self) -> None:
        indicators = make_indicators(close_price=40_000.0, atr_14=400.0, resistances=[40_600.0])
        risk = RiskManager().calculate(SignalDirection.LONG, indicators)
        assert risk is not None
        assert risk.take_profit_1 < 40_600.0

    def test_targets_stay_ordered_even_with_many_levels(self) -> None:
        indicators = make_indicators(
            close_price=40_000.0,
            atr_14=400.0,
            resistances=[40_100.0, 40_150.0, 40_200.0, 40_250.0],
        )
        risk = RiskManager().calculate(SignalDirection.LONG, indicators)
        assert risk is not None
        assert risk.take_profit_1 < risk.take_profit_2 < risk.take_profit_3


class TestRiskRewardRatio:
    def test_ratio_matches_tp2_over_risk(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        distance = risk.entry_low - risk.stop_loss
        expected = (risk.take_profit_2 - risk.entry_low) / distance
        assert risk.risk_reward_ratio == pytest.approx(expected)

    def test_clean_setup_reaches_minimum_ratio(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        assert risk.risk_reward_ratio >= 2.0

    def test_warns_when_ratio_below_minimum(self) -> None:
        manager = RiskManager(RiskConfig(min_risk_reward_ratio=5.0))
        risk = manager.calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        assert risk.risk_reward_ratio < 5.0
        assert any("Chance-Risiko" in warning for warning in risk.warnings)


class TestPositionSize:
    def test_position_size_respects_risk_budget(self) -> None:
        manager = RiskManager(RiskConfig(reference_capital=10_000.0, max_risk_percent=1.0))
        risk = manager.calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        distance = risk.entry_low - risk.stop_loss
        # 1 Prozent von 10.000 sind 100 Einheiten Risiko.
        assert risk.suggested_position_size * distance == pytest.approx(100.0)

    def test_higher_risk_percent_allows_larger_position(self) -> None:
        small = RiskManager(RiskConfig(max_risk_percent=0.5)).calculate(
            SignalDirection.LONG, make_indicators()
        )
        large = RiskManager(RiskConfig(max_risk_percent=2.0)).calculate(
            SignalDirection.LONG, make_indicators()
        )
        assert small is not None and large is not None
        assert large.suggested_position_size > small.suggested_position_size

    def test_wider_stop_reduces_position_size(self) -> None:
        tight = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=200.0))
        wide = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=800.0))
        assert tight is not None and wide is not None
        assert wide.suggested_position_size < tight.suggested_position_size


class TestNoResult:
    @pytest.mark.parametrize(
        "direction",
        [SignalDirection.NEUTRAL, SignalDirection.NO_TRADE],
    )
    def test_returns_none_for_non_actionable_direction(self, direction: SignalDirection) -> None:
        assert RiskManager().calculate(direction, make_indicators()) is None

    def test_returns_none_without_atr(self) -> None:
        assert RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=None)) is None

    def test_returns_none_for_zero_price(self) -> None:
        assert (
            RiskManager().calculate(SignalDirection.LONG, make_indicators(close_price=0.0)) is None
        )


class TestEntryZone:
    def test_entry_zone_brackets_reference_price(self) -> None:
        risk = RiskManager().calculate(SignalDirection.LONG, make_indicators())
        assert risk is not None
        assert risk.entry_low < 40_000.0 < risk.entry_high
        assert risk.entry_mid == pytest.approx(40_000.0)

    def test_entry_zone_width_scales_with_atr(self) -> None:
        narrow = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=100.0))
        wide = RiskManager().calculate(SignalDirection.LONG, make_indicators(atr_14=900.0))
        assert narrow is not None and wide is not None
        assert (wide.entry_high - wide.entry_low) > (narrow.entry_high - narrow.entry_low)


class TestInvalidation:
    def test_note_references_confirmation_timeframe_and_stop(self) -> None:
        risk = RiskManager().calculate(
            SignalDirection.LONG, make_indicators(), confirmation_timeframe="4h"
        )
        assert risk is not None
        assert "4h" in risk.invalidation_note
        assert "unter" in risk.invalidation_note

    def test_short_note_uses_opposite_relation(self) -> None:
        risk = RiskManager().calculate(SignalDirection.SHORT, make_indicators())
        assert risk is not None
        assert "ueber" in risk.invalidation_note
