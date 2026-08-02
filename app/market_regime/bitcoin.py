"""Multi-timeframe Bitcoin market analyzer."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from app.core.enums import TrendDirection
from app.indicators.engine import IndicatorEngine, IndicatorSet
from app.market_regime.structure import build_structure_snapshot
from app.market_regime.types import (
    BitcoinAnalysis,
    MarketBias,
    TimeframeBiasSnapshot,
    bias_from_score,
)

#: Default MTF set and relative weights for the aggregated BTC bias.
DEFAULT_BTC_TIMEFRAMES: tuple[str, ...] = ("1h", "4h", "1d", "1w")
DEFAULT_TF_WEIGHTS: dict[str, float] = {
    "1w": 0.35,
    "1d": 0.30,
    "4h": 0.25,
    "1h": 0.10,
    "12h": 0.20,
    "15m": 0.05,
}


class BitcoinAnalyzer:
    """Score BTC across multiple timeframes into a single market bias."""

    def __init__(
        self,
        *,
        timeframes: tuple[str, ...] = DEFAULT_BTC_TIMEFRAMES,
        tf_weights: Mapping[str, float] | None = None,
        indicator_engine: IndicatorEngine | None = None,
    ) -> None:
        self._timeframes = timeframes
        self._tf_weights = dict(tf_weights or DEFAULT_TF_WEIGHTS)
        self._indicators = indicator_engine or IndicatorEngine(min_candles=50)

    @property
    def timeframes(self) -> tuple[str, ...]:
        return self._timeframes

    def analyze_from_frames(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        symbol: str = "BTCUSDT",
    ) -> BitcoinAnalysis:
        """Compute indicators per TF from OHLCV frames and aggregate bias."""
        sets: dict[str, IndicatorSet] = {}
        structure_frames: dict[str, pd.DataFrame] = {}
        for tf in self._timeframes:
            frame = frames.get(tf)
            if frame is None or len(frame) < 30:
                continue
            try:
                sets[tf] = self._indicators.compute(frame, tf, symbol=symbol, strict=False)
                structure_frames[tf] = frame
            except Exception:
                continue
        return self.analyze_from_indicators(sets, structure_frames=structure_frames)

    def analyze_from_indicators(
        self,
        indicator_sets: Mapping[str, IndicatorSet],
        *,
        structure_frames: Mapping[str, pd.DataFrame] | None = None,
    ) -> BitcoinAnalysis:
        if not indicator_sets:
            return BitcoinAnalysis(
                available=False,
                bias=MarketBias.NEUTRAL,
                trend="neutral",
                score=0.0,
                price=None,
                detail="btc_indicators_missing",
            )

        tf_snaps: dict[str, TimeframeBiasSnapshot] = {}
        weighted = 0.0
        weight_sum = 0.0

        for tf, indicators in indicator_sets.items():
            snap = self._score_timeframe(
                tf,
                indicators,
                frame=(structure_frames or {}).get(tf),
            )
            tf_snaps[tf] = snap
            w = float(self._tf_weights.get(tf, 0.1))
            weighted += snap.score * w
            weight_sum += w

        agg = weighted / weight_sum if weight_sum > 0 else 0.0
        bias = bias_from_score(agg)
        price = next(iter(tf_snaps.values())).close if tf_snaps else None
        # Prefer higher-TF trend label when available.
        trend_source = (
            tf_snaps.get("1d")
            or tf_snaps.get("4h")
            or tf_snaps.get("1w")
            or next(iter(tf_snaps.values()))
        )
        return BitcoinAnalysis(
            available=True,
            bias=bias,
            trend=trend_source.trend,
            score=round(agg, 2),
            price=price,
            timeframes=tf_snaps,
            detail=(
                f"btc_mtf score={agg:.1f} bias={bias.value} "
                f"tfs={','.join(sorted(tf_snaps))}"
            ),
        )

    def _score_timeframe(
        self,
        timeframe: str,
        indicators: IndicatorSet,
        *,
        frame: pd.DataFrame | None,
    ) -> TimeframeBiasSnapshot:
        close = float(indicators.close_price)
        votes = 0.0
        total = 0.0

        def _vote(condition: bool | None, weight: float = 1.0) -> None:
            nonlocal votes, total
            if condition is None:
                return
            total += weight
            votes += weight if condition else -weight

        ema20, ema50, ema200 = indicators.ema_20, indicators.ema_50, indicators.ema_200
        _vote(close > ema20 if ema20 is not None else None, 1.0)
        _vote(close > ema50 if ema50 is not None else None, 1.2)
        _vote(close > ema200 if ema200 is not None else None, 1.4)
        _vote(ema20 > ema50 if ema20 is not None and ema50 is not None else None, 1.2)
        _vote(ema50 > ema200 if ema50 is not None and ema200 is not None else None, 1.0)

        st = indicators.supertrend_direction
        if st is not None:
            _vote(st > 0, 1.3)

        rsi = indicators.rsi_14
        if rsi is not None:
            if rsi >= 55:
                _vote(True, 0.8)
            elif rsi <= 45:
                _vote(False, 0.8)

        macd_hist = indicators.macd_histogram
        if macd_hist is not None:
            _vote(macd_hist > 0, 0.9)

        if indicators.plus_di is not None and indicators.minus_di is not None:
            _vote(indicators.plus_di > indicators.minus_di, 0.8)

        structure = build_structure_snapshot(frame if frame is not None else pd.DataFrame(), indicators.structure)
        if structure.higher_highs and structure.higher_lows:
            _vote(True, 1.2)
        elif structure.lower_highs and structure.lower_lows:
            _vote(False, 1.2)
        if structure.bos_bullish or structure.choch_bullish:
            _vote(True, 0.7)
        if structure.bos_bearish or structure.choch_bearish:
            _vote(False, 0.7)

        if indicators.vwap is not None:
            _vote(close > indicators.vwap, 0.5)

        score = 0.0 if total <= 0 else max(-100.0, min(100.0, (votes / total) * 100.0))
        bias = bias_from_score(score)

        trend = "neutral"
        if indicators.trend_direction is TrendDirection.BULLISH:
            trend = "bullish"
        elif indicators.trend_direction is TrendDirection.BEARISH:
            trend = "bearish"
        elif bias in (MarketBias.STRONG_BULLISH, MarketBias.BULLISH):
            trend = "bullish"
        elif bias in (MarketBias.STRONG_BEARISH, MarketBias.BEARISH):
            trend = "bearish"

        momentum = indicators.roc_14
        volatility = indicators.atr_percent
        return TimeframeBiasSnapshot(
            timeframe=timeframe,
            bias=bias,
            trend=trend,
            score=round(score, 2),
            close=close,
            ema_20=ema20,
            ema_50=ema50,
            ema_200=ema200,
            rsi=rsi,
            macd_histogram=macd_hist,
            atr_percent=indicators.atr_percent,
            adx=indicators.adx_14,
            volume_ratio=indicators.volume_ratio,
            vwap=indicators.vwap,
            momentum=momentum,
            trend_strength=indicators.trend_strength,
            volatility=volatility,
            structure=structure,
        )
