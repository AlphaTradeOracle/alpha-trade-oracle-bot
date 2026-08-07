"""Tests fuer die Multi-Timeframe-Datenqualitaet."""

from __future__ import annotations

from app.signals.data_quality import compute_analysis_data_quality, has_required_timeframe_coverage


class TestDataQualityCoverage:
    def test_does_not_penalize_missing_unrequested_higher_tf(self) -> None:
        indicator_sets = {"1h": object(), "4h": object()}
        quality = compute_analysis_data_quality(
            [90.0, 85.0],
            indicator_sets=indicator_sets,
            primary_timeframe="1h",
        )
        assert quality == 87.5

    def test_primary_only_keeps_mean_quality(self) -> None:
        """Fehlender HTF blockiert nicht mehr via kuenstlichem 59.99-Cap."""
        indicator_sets = {"1h": object()}
        assert not has_required_timeframe_coverage(
            indicator_sets, primary_timeframe="1h"
        )
        assert compute_analysis_data_quality(
            [95.0],
            indicator_sets=indicator_sets,
            primary_timeframe="1h",
        ) == 95.0

    def test_missing_primary_returns_zero(self) -> None:
        indicator_sets = {"4h": object()}
        assert compute_analysis_data_quality(
            [90.0],
            indicator_sets=indicator_sets,
            primary_timeframe="1h",
        ) == 0.0

    def test_young_listing_with_1h_and_4h_keeps_quality(self) -> None:
        indicator_sets = {"1h": object(), "4h": object()}
        quality = compute_analysis_data_quality(
            [80.0, 75.0],
            indicator_sets=indicator_sets,
            primary_timeframe="1h",
        )
        assert quality == 77.5
