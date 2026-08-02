"""Ethereum analyzer — trend/momentum/relative strength vs BTC (when frames given)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.indicators.engine import IndicatorEngine
from app.market.types import AnalyzerResult, bias_from_signed


class EthereumAnalyzer:
    name = "ethereum"

    def __init__(self, *, timeframe: str = "4h", min_candles: int = 210) -> None:
        self._timeframe = timeframe
        self._engine = IndicatorEngine(min_candles=min_candles)

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof, symbol
        if not frames or self._timeframe not in frames:
            return AnalyzerResult(
                name=self.name,
                available=False,
                score=0.0,
                detail="eth_frames_missing — module ready, awaiting data",
            )
        df = frames[self._timeframe]
        try:
            ind = self._engine.compute(df, self._timeframe, symbol="ETHUSDT")
        except Exception as exc:  # noqa: BLE001
            return AnalyzerResult(
                name=self.name, available=False, score=0.0, detail=str(exc)
            )

        score = 0.0
        if ind.ema_20 and ind.close_price > ind.ema_20:
            score += 25
        elif ind.ema_20:
            score -= 25
        if ind.ema_50 and ind.close_price > ind.ema_50:
            score += 25
        elif ind.ema_50:
            score -= 25
        if ind.rsi_14 is not None:
            score += 15 if ind.rsi_14 >= 55 else (-15 if ind.rsi_14 <= 45 else 0)
        if ind.macd_histogram is not None:
            score += 15 if ind.macd_histogram > 0 else -15

        # Relative strength vs BTC when both closes present in metrics bag
        btc_df = frames.get("btc_4h")
        if btc_df is None:
            btc_df = frames.get("BTCUSDT_4h")
        rel = None
        if btc_df is not None and len(btc_df) >= 20 and len(df) >= 20:
            eth_ret = float(df["close"].iloc[-1] / df["close"].iloc[-20] - 1.0)
            btc_ret = float(btc_df["close"].iloc[-1] / btc_df["close"].iloc[-20] - 1.0)
            rel = eth_ret - btc_ret
            score += 20 if rel > 0 else -20

        score = max(-100.0, min(100.0, score))
        return AnalyzerResult(
            name=self.name,
            available=True,
            score=round(score, 2),
            bias=bias_from_signed(score),
            detail=f"ETH {self._timeframe} lean={score:+.0f}",
            metrics={
                "close": ind.close_price,
                "rsi": ind.rsi_14,
                "relativeStrengthVsBtc20": rel,
            },
        )
