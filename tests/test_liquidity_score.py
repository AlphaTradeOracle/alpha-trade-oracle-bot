"""Unit tests for free-venue Liquidity Score (no network)."""

from __future__ import annotations

import pandas as pd

from app.market_regime.liquidity import (
    LiquidityScoreCalculator,
    VenueLiquiditySnapshot,
)


def test_crowded_longs_negative_score() -> None:
    snaps = [
        VenueLiquiditySnapshot(
            venue="binance",
            funding_rate=0.001,
            long_share=0.62,
            book_imbalance=-0.2,
            volume_24h=5e9,
        ),
        VenueLiquiditySnapshot(
            venue="bybit",
            funding_rate=0.0008,
            long_share=0.60,
            book_imbalance=-0.1,
            volume_24h=2e9,
        ),
    ]
    result = LiquidityScoreCalculator().compute(snaps)
    assert result.available
    assert result.score < 0
    assert "binance" in result.venues
    assert "funding" in result.components


def test_bid_heavy_book_and_short_crowd_positive() -> None:
    snaps = [
        VenueLiquiditySnapshot(
            venue="hyperliquid",
            funding_rate=-0.0004,
            long_share=0.42,
            book_imbalance=0.35,
            volume_24h=1e9,
        )
    ]
    result = LiquidityScoreCalculator().compute(snaps)
    assert result.available
    assert result.score > 0


def test_wick_only_fallback() -> None:
    idx = pd.date_range("2024-01-01", periods=40, freq="h", tz="UTC")
    close = [float(100 + i) for i in range(40)]
    # Last bar: deep lower wick (long-liquidation proxy).
    frame = pd.DataFrame(
        {
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close[:-1]] + [close[-1] - 8],
            "close": close,
            "volume": [1000.0] * 40,
        },
        index=idx,
    )
    result = LiquidityScoreCalculator().compute([], btc_frame=frame)
    assert result.available
    assert result.wick_long_pressure is not None
    assert result.wick_long_pressure > 0
    assert result.score > 0
