"""Tests der Indikatorberechnung.

Die Indikatoren werden gegen von Hand nachvollziehbare Referenzwerte geprueft,
nicht gegen eine andere Bibliothek — sonst wuerde nur eine Implementierung mit
einer anderen verglichen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.enums import TrendDirection
from app.core.errors import InsufficientDataError
from app.indicators.engine import IndicatorEngine
from app.indicators.momentum import macd, rate_of_change, rsi, stochastic_rsi
from app.indicators.structure import find_swing_points
from app.indicators.trend import adx, atr, ema, sma, supertrend
from app.indicators.volatility import bollinger_bands
from app.indicators.volume import on_balance_volume, volume_moving_average, volume_ratio, vwap


class TestMovingAverages:
    def test_sma_matches_manual_mean(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(series, 3)
        assert pd.isna(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)

    def test_ema_weights_recent_values_more(self) -> None:
        series = pd.Series([10.0] * 20 + [20.0] * 20)
        result = ema(series, 10)
        simple = sma(series, 10)
        # Nach dem Sprung muss die EMA schneller reagieren als die SMA.
        assert result.iloc[25] > simple.iloc[25]

    def test_ema_of_constant_series_is_constant(self) -> None:
        series = pd.Series([42.0] * 50)
        assert ema(series, 20).iloc[-1] == pytest.approx(42.0)


class TestRSI:
    def test_rsi_is_100_for_monotonic_rise(self) -> None:
        series = pd.Series(np.arange(1.0, 60.0))
        assert rsi(series, 14).iloc[-1] == pytest.approx(100.0)

    def test_rsi_is_zero_for_monotonic_fall(self) -> None:
        series = pd.Series(np.arange(60.0, 1.0, -1.0))
        assert rsi(series, 14).iloc[-1] == pytest.approx(0.0)

    def test_rsi_stays_within_bounds(self, uptrend_df: pd.DataFrame) -> None:
        values = rsi(uptrend_df["close"], 14).dropna()
        assert not values.empty
        assert values.between(0.0, 100.0).all()


class TestMACD:
    def test_histogram_is_difference_of_line_and_signal(self, uptrend_df: pd.DataFrame) -> None:
        line, signal, histogram = macd(uptrend_df["close"])
        difference = (line - signal).dropna()
        assert histogram.dropna().sub(difference).abs().max() == pytest.approx(0.0, abs=1e-9)

    def test_macd_positive_in_uptrend(self, uptrend_df: pd.DataFrame) -> None:
        line, _signal, _hist = macd(uptrend_df["close"])
        assert line.iloc[-1] > 0


class TestStochasticRSI:
    def test_values_within_bounds(self, uptrend_df: pd.DataFrame) -> None:
        k, d = stochastic_rsi(uptrend_df["close"])
        assert k.dropna().between(0.0, 100.0).all()
        assert d.dropna().between(0.0, 100.0).all()


class TestRateOfChange:
    def test_roc_matches_manual_percentage(self) -> None:
        series = pd.Series([100.0, 101.0, 102.0, 110.0])
        # 110 gegenueber 100 entspricht +10 Prozent.
        assert rate_of_change(series, 3).iloc[-1] == pytest.approx(10.0)


class TestATR:
    def test_atr_is_positive_and_finite(self, uptrend_df: pd.DataFrame) -> None:
        result = atr(uptrend_df["high"], uptrend_df["low"], uptrend_df["close"], 14)
        assert result.dropna().gt(0).all()
        assert np.isfinite(result.iloc[-1])

    def test_atr_grows_with_wider_ranges(self) -> None:
        close = pd.Series([100.0] * 40)
        narrow_high, narrow_low = close + 1.0, close - 1.0
        wide_high, wide_low = close + 5.0, close - 5.0
        narrow = atr(narrow_high, narrow_low, close, 14).iloc[-1]
        wide = atr(wide_high, wide_low, close, 14).iloc[-1]
        assert wide > narrow


class TestADX:
    def test_adx_within_bounds(self, uptrend_df: pd.DataFrame) -> None:
        adx_series, plus_di, minus_di = adx(
            uptrend_df["high"], uptrend_df["low"], uptrend_df["close"], 14
        )
        assert adx_series.dropna().between(0.0, 100.0).all()
        assert plus_di.dropna().between(0.0, 100.0).all()
        assert minus_di.dropna().between(0.0, 100.0).all()

    def test_plus_di_dominates_in_uptrend(self, uptrend_df: pd.DataFrame) -> None:
        _adx, plus_di, minus_di = adx(
            uptrend_df["high"], uptrend_df["low"], uptrend_df["close"], 14
        )
        assert plus_di.iloc[-1] > minus_di.iloc[-1]


class TestBollingerBands:
    def test_bands_are_ordered(self, uptrend_df: pd.DataFrame) -> None:
        upper, middle, lower, width = bollinger_bands(uptrend_df["close"])
        valid = upper.notna()
        assert (upper[valid] >= middle[valid]).all()
        assert (middle[valid] >= lower[valid]).all()
        assert width.dropna().ge(0).all()

    def test_width_is_zero_for_constant_series(self) -> None:
        series = pd.Series([100.0] * 40)
        _upper, _middle, _lower, width = bollinger_bands(series)
        assert width.iloc[-1] == pytest.approx(0.0)


class TestVolume:
    def test_obv_rises_when_price_rises(self) -> None:
        close = pd.Series([10.0, 11.0, 12.0, 13.0])
        volume = pd.Series([100.0, 100.0, 100.0, 100.0])
        result = on_balance_volume(close, volume)
        assert result.iloc[-1] == pytest.approx(300.0)

    def test_obv_falls_when_price_falls(self) -> None:
        close = pd.Series([13.0, 12.0, 11.0, 10.0])
        volume = pd.Series([100.0, 100.0, 100.0, 100.0])
        assert on_balance_volume(close, volume).iloc[-1] == pytest.approx(-300.0)

    def test_volume_ratio_is_one_for_constant_volume(self) -> None:
        volume = pd.Series([500.0] * 40)
        assert volume_ratio(volume, 20).iloc[-1] == pytest.approx(1.0)

    def test_volume_moving_average(self) -> None:
        volume = pd.Series([100.0] * 10 + [200.0] * 10)
        assert volume_moving_average(volume, 20).iloc[-1] == pytest.approx(150.0)

    def test_vwap_lies_within_price_range(self, uptrend_df: pd.DataFrame) -> None:
        result = vwap(
            uptrend_df["high"],
            uptrend_df["low"],
            uptrend_df["close"],
            uptrend_df["volume"],
            uptrend_df.index,
        )
        last = result.iloc[-1]
        # Der VWAP wird taeglich zurueckgesetzt und muss im Tagesbereich liegen.
        day = uptrend_df[uptrend_df.index.date == uptrend_df.index[-1].date()]
        assert day["low"].min() <= last <= day["high"].max()


class TestSupertrend:
    def test_direction_is_bullish_in_uptrend(self, uptrend_df: pd.DataFrame) -> None:
        _line, direction = supertrend(uptrend_df["high"], uptrend_df["low"], uptrend_df["close"])
        assert direction.iloc[-1] == 1

    def test_direction_is_bearish_in_downtrend(self, downtrend_df: pd.DataFrame) -> None:
        _line, direction = supertrend(
            downtrend_df["high"], downtrend_df["low"], downtrend_df["close"]
        )
        assert direction.iloc[-1] == -1

    def test_line_is_below_price_when_bullish(self, uptrend_df: pd.DataFrame) -> None:
        line, direction = supertrend(uptrend_df["high"], uptrend_df["low"], uptrend_df["close"])
        assert direction.iloc[-1] == 1
        assert line.iloc[-1] < uptrend_df["close"].iloc[-1]


class TestSwingPoints:
    def test_finds_obvious_peak_and_valley(self) -> None:
        highs = pd.Series([1.0, 2.0, 3.0, 10.0, 3.0, 2.0, 1.0, 0.5, 2.0, 3.0, 4.0])
        lows = highs - 0.5
        points = find_swing_points(highs, lows, left=3, right=3)

        swing_highs = [p.index for p in points if p.is_high]
        swing_lows = [p.index for p in points if not p.is_high]
        assert 3 in swing_highs
        assert 7 in swing_lows

    def test_no_swings_in_monotonic_series(self) -> None:
        values = pd.Series(np.arange(1.0, 30.0))
        points = find_swing_points(values, values - 0.5, left=3, right=3)
        assert [p for p in points if p.is_high] == []


class TestIndicatorEngine:
    def test_computes_full_set_for_uptrend(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        result = indicator_engine.compute(uptrend_df, "1h", symbol="BTCUSDT")

        assert result.timeframe == "1h"
        assert result.close_price > 0
        for attribute in (
            "ema_9",
            "ema_20",
            "ema_50",
            "ema_100",
            "ema_200",
            "sma_50",
            "sma_200",
            "rsi_14",
            "macd",
            "bb_upper",
            "atr_14",
            "adx_14",
            "stoch_rsi_k",
            "obv",
            "volume_ma_20",
            "roc_14",
            "supertrend",
            "vwap",
        ):
            assert getattr(result, attribute) is not None, f"{attribute} fehlt"

        assert result.completeness == pytest.approx(100.0)
        assert result.trend_direction is TrendDirection.BULLISH
        assert 0.0 <= result.trend_strength <= 100.0

    def test_detects_downtrend(
        self, indicator_engine: IndicatorEngine, downtrend_df: pd.DataFrame
    ) -> None:
        result = indicator_engine.compute(downtrend_df, "1h", symbol="BTCUSDT")
        assert result.trend_direction is TrendDirection.BEARISH

    def test_ema_stack_is_ordered_in_uptrend(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        result = indicator_engine.compute(uptrend_df, "1h")
        assert result.ema_9 is not None and result.ema_200 is not None
        assert result.ema_9 > result.ema_20 > result.ema_50 > result.ema_200  # type: ignore[operator]

    def test_vwap_only_for_intraday_timeframes(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        intraday = indicator_engine.compute(uptrend_df, "1h")
        daily = indicator_engine.compute(uptrend_df, "1d")
        assert intraday.vwap is not None
        assert daily.vwap is None
        # Der fehlende VWAP darf die Vollstaendigkeit nicht senken.
        assert daily.completeness == pytest.approx(100.0)

    def test_raises_on_too_short_history(self, indicator_engine: IndicatorEngine) -> None:
        short = pd.DataFrame(
            {
                "open": [1.0] * 10,
                "high": [1.0] * 10,
                "low": [1.0] * 10,
                "close": [1.0] * 10,
                "volume": [1.0] * 10,
            }
        )
        with pytest.raises(InsufficientDataError):
            indicator_engine.compute(short, "1h", symbol="BTCUSDT")

    def test_tolerates_short_history_when_not_strict(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        """Der Backtest iteriert bewusst kurze Fenster."""
        result = indicator_engine.compute(uptrend_df.iloc[:60], "1h", strict=False)
        assert result.ema_20 is not None
        assert result.ema_200 is None
        assert result.completeness < 100.0

    def test_raises_on_missing_columns(self, indicator_engine: IndicatorEngine) -> None:
        with pytest.raises(ValueError, match="fehlen Spalten"):
            indicator_engine.compute(pd.DataFrame({"close": [1.0] * 300}), "1h")

    def test_indicators_used_lists_only_computed(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        result = indicator_engine.compute(uptrend_df.iloc[:60], "1d", strict=False)
        used = result.indicators_used()
        assert "EMA20" in used
        assert "EMA200" not in used
        assert "VWAP" not in used

    def test_is_deterministic(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        first = indicator_engine.compute(uptrend_df, "1h")
        second = indicator_engine.compute(uptrend_df, "1h")
        assert first.to_snapshot_dict() == second.to_snapshot_dict()

    def test_snapshot_dict_is_flat_and_serializable(
        self, indicator_engine: IndicatorEngine, uptrend_df: pd.DataFrame
    ) -> None:
        snapshot = indicator_engine.compute(uptrend_df, "1h").to_snapshot_dict()
        for key, value in snapshot.items():
            assert isinstance(value, (int, float, str, type(None))), key
