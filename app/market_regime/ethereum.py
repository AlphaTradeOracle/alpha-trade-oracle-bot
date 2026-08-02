"""Ethereum relative-strength analyzer (vs BTC)."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from app.indicators.engine import IndicatorEngine
from app.market_regime.bitcoin import BitcoinAnalyzer
from app.market_regime.types import EthereumAnalysis, MarketBias, bias_from_score


class EthereumAnalyzer:
    def __init__(
        self,
        *,
        indicator_engine: IndicatorEngine | None = None,
        timeframes: tuple[str, ...] = ("1h", "4h", "1d"),
    ) -> None:
        self._btc_like = BitcoinAnalyzer(
            timeframes=timeframes,
            indicator_engine=indicator_engine,
        )

    def analyze(
        self,
        eth_frames: Mapping[str, pd.DataFrame],
        btc_frames: Mapping[str, pd.DataFrame] | None = None,
        *,
        symbol: str = "ETHUSDT",
    ) -> EthereumAnalysis:
        if not eth_frames:
            return EthereumAnalysis(available=False, detail="eth_frames_missing")

        eth = self._btc_like.analyze_from_frames(eth_frames, symbol=symbol)
        if not eth.available:
            return EthereumAnalysis(available=False, detail=eth.detail)

        rel: float | None = None
        if btc_frames:
            tf = "1d" if "1d" in eth_frames and "1d" in btc_frames else (
                "4h" if "4h" in eth_frames and "4h" in btc_frames else None
            )
            if tf is not None:
                rel = _relative_strength(eth_frames[tf], btc_frames[tf])

        score = eth.score
        if rel is not None:
            # Blend a bit of relative strength into the ETH market contribution.
            score = max(-100.0, min(100.0, 0.75 * eth.score + 0.25 * rel))

        bias = bias_from_score(score)
        return EthereumAnalysis(
            available=True,
            bias=bias,
            trend=eth.trend,
            score=round(score, 2),
            relative_strength_vs_btc=None if rel is None else round(rel, 2),
            detail=f"eth score={score:.1f} rel_btc={rel}",
        )


def _relative_strength(eth: pd.DataFrame, btc: pd.DataFrame, lookback: int = 20) -> float | None:
    if "close" not in eth.columns or "close" not in btc.columns:
        return None
    if len(eth) < lookback + 1 or len(btc) < lookback + 1:
        return None
    eth_ret = float(eth["close"].iloc[-1]) / float(eth["close"].iloc[-lookback]) - 1.0
    btc_ret = float(btc["close"].iloc[-1]) / float(btc["close"].iloc[-lookback]) - 1.0
    delta = eth_ret - btc_ret
    return max(-100.0, min(100.0, delta * 500.0))
