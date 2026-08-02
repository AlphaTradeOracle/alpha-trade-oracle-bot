"""Bitcoin multi-timeframe market analyzer."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.core.enums import StructureState, TrendDirection
from app.indicators.engine import IndicatorEngine, IndicatorSet
from app.market.types import AnalyzerResult, MarketBias, bias_from_signed

#: Minimum TFs for a usable BTC bias (1w may be absent in DB).
DEFAULT_BTC_TIMEFRAMES = ("1h", "4h", "1d", "1w")
TF_WEIGHTS = {"1h": 0.15, "4h": 0.30, "1d": 0.35, "1w": 0.20}


class BitcoinAnalyzer:
    name = "bitcoin"

    def __init__(
        self,
        *,
        timeframes: tuple[str, ...] = DEFAULT_BTC_TIMEFRAMES,
        min_candles: int = 210,
    ) -> None:
        self._timeframes = timeframes
        self._engine = IndicatorEngine(min_candles=min_candles)

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, symbol  # frames are already truncated to asof by the caller
        if not frames:
            return AnalyzerResult(
                name=self.name,
                available=False,
                score=0.0,
                detail="btc_frames_missing",
            )

        tf_scores: dict[str, float] = {}
        tf_metrics: dict[str, dict] = {}
        notes: list[str] = []

        for tf in self._timeframes:
            df = frames.get(tf)
            if df is None or df.empty:
                # Synthesize weekly lean from daily when 1w candles are absent.
                if tf == "1w" and "1d" in frames and frames["1d"] is not None:
                    syn = self._synthetic_weekly(frames["1d"])
                    if syn is not None:
                        tf_scores[tf] = syn[0]
                        tf_metrics[tf] = syn[1]
                        notes.append("1w synthesized from 1d")
                continue
            try:
                indicators = self._engine.compute(df, tf, symbol="BTCUSDT")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{tf}: {exc}")
                continue
            score, metrics = self._score_indicators(indicators)
            tf_scores[tf] = score
            tf_metrics[tf] = metrics

        if not tf_scores:
            return AnalyzerResult(
                name=self.name,
                available=False,
                score=0.0,
                detail="btc_indicators_unavailable",
            )

        weight_sum = 0.0
        weighted = 0.0
        for tf, score in tf_scores.items():
            w = TF_WEIGHTS.get(tf, 0.2)
            weighted += score * w
            weight_sum += w
        aggregate = weighted / weight_sum if weight_sum else 0.0
        bias = bias_from_signed(aggregate)

        detail_parts = [f"{tf}={score:+.0f}" for tf, score in sorted(tf_scores.items())]
        if notes:
            detail_parts.extend(notes)

        return AnalyzerResult(
            name=self.name,
            available=True,
            score=round(max(-100.0, min(100.0, aggregate)), 2),
            bias=bias,
            detail="BTC MTF " + ", ".join(detail_parts),
            metrics={
                "timeframes": tf_metrics,
                "aggregateScore": round(aggregate, 2),
                "bias": bias.value,
            },
        )

    def _score_indicators(self, ind: IndicatorSet) -> tuple[float, dict]:
        score = 0.0
        # Trend / EMAs
        if ind.ema_20 is not None and ind.close_price > ind.ema_20:
            score += 12
        elif ind.ema_20 is not None:
            score -= 12
        if ind.ema_50 is not None and ind.close_price > ind.ema_50:
            score += 12
        elif ind.ema_50 is not None:
            score -= 12
        if ind.ema_200 is not None and ind.close_price > ind.ema_200:
            score += 14
        elif ind.ema_200 is not None:
            score -= 14
        if ind.ema_20 is not None and ind.ema_50 is not None:
            score += 8 if ind.ema_20 > ind.ema_50 else -8

        if ind.trend_direction is TrendDirection.BULLISH:
            score += 10
        elif ind.trend_direction is TrendDirection.BEARISH:
            score -= 10

        # Structure HH/HL vs LH/LL + breakouts (BOS proxy)
        st = ind.structure
        if st.state is StructureState.HH_HL:
            score += 16
        elif st.state is StructureState.LH_LL:
            score -= 16
        if st.breakout_up:
            score += 10  # BOS up
        if st.breakout_down:
            score -= 10  # BOS down
        if st.failed_breakout_up:
            score -= 8  # CHoCH-ish
        if st.failed_breakout_down:
            score += 8
        if st.bullish_divergence:
            score += 6
        if st.bearish_divergence:
            score -= 6

        # Momentum
        if ind.rsi_14 is not None:
            if ind.rsi_14 >= 55:
                score += 6
            elif ind.rsi_14 <= 45:
                score -= 6
        if ind.macd_histogram is not None:
            score += 6 if ind.macd_histogram > 0 else -6

        # Volume / VWAP
        if ind.volume_ratio is not None:
            if ind.volume_ratio >= 1.2:
                # amplify current directional lean slightly
                score += 4 if score >= 0 else -4
            elif ind.volume_ratio < 0.6:
                score *= 0.85  # low-volume moves less trustworthy
        if ind.vwap is not None:
            score += 5 if ind.close_price >= ind.vwap else -5

        # Volatility context (ATR%) — extreme vol dampens conviction
        if ind.atr_percent is not None and ind.atr_percent > 4.0:
            score *= 0.9

        score = max(-100.0, min(100.0, score))
        metrics = {
            "close": ind.close_price,
            "ema20": ind.ema_20,
            "ema50": ind.ema_50,
            "ema200": ind.ema_200,
            "rsi": ind.rsi_14,
            "macdHist": ind.macd_histogram,
            "atrPercent": ind.atr_percent,
            "volumeRatio": ind.volume_ratio,
            "vwap": ind.vwap,
            "structure": st.state.value if st.state else None,
            "breakoutUp": st.breakout_up,
            "breakoutDown": st.breakout_down,
            "trend": ind.trend_direction.value,
            "trendStrength": ind.trend_strength,
            "nearestSupport": st.nearest_support,
            "nearestResistance": st.nearest_resistance,
            # Placeholders for later SMC modules
            "orderBlocks": None,
            "fairValueGaps": None,
            "liquidityZones": None,
            "bos": "up" if st.breakout_up else ("down" if st.breakout_down else None),
            "choch": (
                "bearish"
                if st.failed_breakout_up
                else ("bullish" if st.failed_breakout_down else None)
            ),
        }
        return score, metrics

    def _synthetic_weekly(self, daily: pd.DataFrame) -> tuple[float, dict] | None:
        if len(daily) < 20:
            return None
        # Resample last ~60 days into weekly bars for a coarse lean.
        df = daily.copy()
        if "open_time" in df.columns:
            df = df.set_index(pd.to_datetime(df["open_time"], utc=True))
        elif not isinstance(df.index, pd.DatetimeIndex):
            return None
        weekly = (
            df.resample("1W")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        if len(weekly) < 12:
            return None
        try:
            indicators = self._engine.compute(weekly, "1w", symbol="BTCUSDT")
        except Exception:
            # Fallback: simple close vs SMA
            close = float(weekly["close"].iloc[-1])
            sma = float(weekly["close"].tail(10).mean())
            score = 30.0 if close >= sma else -30.0
            return score, {"close": close, "sma10": sma, "synthetic": True}
        return self._score_indicators(indicators)
