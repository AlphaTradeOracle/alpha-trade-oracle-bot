"""Unit tests for Market Regime Filter scoring and BTC bias aggregation."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.core.enums import SignalDirection
from app.market_regime.adapter import bias_to_market_regime, hard_veto_reason, to_legacy_regime_snapshot
from app.market_regime.bitcoin import BitcoinAnalyzer
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.types import (
    BitcoinAnalysis,
    DominanceAnalysis,
    EthereumAnalysis,
    FearGreedAnalysis,
    FundingAnalysis,
    FundingStatus,
    LiquidationAnalysis,
    MarketBias,
    MarketRegimeSnapshot,
    OpenInterestAnalysis,
    ScoreWeights,
    bias_from_score,
    empty_snapshot,
)
from app.signals.regime import MarketRegime


def _ohlcv(n: int = 260, *, trend: float = 0.002, start: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    closes = start * (1.0 + trend) ** np.arange(n)
    high = closes * 1.01
    low = closes * 0.99
    open_ = closes * 0.999
    volume = np.full(n, 1000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": closes, "volume": volume},
        index=idx,
    )


class TestBiasHelpers:
    def test_bias_from_score_thresholds(self) -> None:
        assert bias_from_score(80) is MarketBias.STRONG_BULLISH
        assert bias_from_score(40) is MarketBias.BULLISH
        assert bias_from_score(0) is MarketBias.NEUTRAL
        assert bias_from_score(-40) is MarketBias.BEARISH
        assert bias_from_score(-80) is MarketBias.STRONG_BEARISH

    def test_bias_to_legacy_regime(self) -> None:
        assert bias_to_market_regime(MarketBias.STRONG_BULLISH) is MarketRegime.BULLISH
        assert bias_to_market_regime(MarketBias.BEARISH) is MarketRegime.BEARISH
        assert bias_to_market_regime(MarketBias.NEUTRAL) is MarketRegime.NEUTRAL


class TestFinalScoreCalculator:
    def _snap(self, *, global_score: float = 50.0, funding: float | None = None) -> MarketRegimeSnapshot:
        return MarketRegimeSnapshot(
            available=True,
            bias=MarketBias.BULLISH,
            btc=BitcoinAnalysis(True, MarketBias.BULLISH, "bullish", 50.0, 100.0),
            eth=EthereumAnalysis(False),
            dominance=DominanceAnalysis(False),
            fear_greed=FearGreedAnalysis(False),
            funding=FundingAnalysis(
                available=funding is not None,
                status=FundingStatus.NEUTRAL,
                score=funding or 0.0,
            ),
            open_interest=OpenInterestAnalysis(False),
            liquidations=LiquidationAnalysis(False),
            global_score=global_score,
            captured_at=datetime(2024, 6, 1, tzinfo=UTC),
        )

    def test_unavailable_keeps_coin_score(self) -> None:
        calc = FinalScoreCalculator(ScoreWeights())
        out = calc.blend(80.0, SignalDirection.STRONG_LONG, empty_snapshot(datetime.now(UTC)))
        assert out.final_score == 80.0
        assert out.weights_used["coin"] == 1.0

    def test_bullish_global_raises_long_score(self) -> None:
        calc = FinalScoreCalculator(
            ScoreWeights(coin=0.6, global_market=0.4, funding=0, open_interest=0, liquidations=0)
        )
        out = calc.blend(70.0, SignalDirection.STRONG_LONG, self._snap(global_score=80.0))
        assert out.final_score > 70.0

    def test_bullish_global_raises_short_score_toward_reject(self) -> None:
        calc = FinalScoreCalculator(
            ScoreWeights(coin=0.6, global_market=0.4, funding=0, open_interest=0, liquidations=0)
        )
        # Shorts use low scores; a bullish market should worsen the short → higher score.
        out = calc.blend(20.0, SignalDirection.STRONG_SHORT, self._snap(global_score=80.0))
        assert out.final_score > 20.0

    def test_bearish_global_strengthens_short_score(self) -> None:
        calc = FinalScoreCalculator(
            ScoreWeights(coin=0.6, global_market=0.4, funding=0, open_interest=0, liquidations=0)
        )
        out = calc.blend(22.0, SignalDirection.STRONG_SHORT, self._snap(global_score=-80.0))
        assert out.final_score < 22.0

    def test_missing_funding_renormalizes(self) -> None:
        calc = FinalScoreCalculator(ScoreWeights())
        out = calc.blend(75.0, SignalDirection.STRONG_LONG, self._snap(global_score=0.0))
        assert out.weights_used["funding"] == 0.0
        assert pytest.approx(sum(out.weights_used.values()), rel=1e-6) == 1.0


class TestBitcoinAnalyzer:
    def test_uptrend_frames_bullish(self) -> None:
        frames = {
            "1h": _ohlcv(300, trend=0.0015),
            "4h": _ohlcv(300, trend=0.004),
            "1d": _ohlcv(300, trend=0.008),
            "1w": _ohlcv(260, trend=0.02),
        }
        result = BitcoinAnalyzer().analyze_from_frames(frames)
        assert result.available
        assert result.bias in (MarketBias.BULLISH, MarketBias.STRONG_BULLISH)
        assert result.score > 0

    def test_downtrend_frames_bearish(self) -> None:
        frames = {
            "1h": _ohlcv(300, trend=-0.0015),
            "4h": _ohlcv(300, trend=-0.004),
            "1d": _ohlcv(300, trend=-0.008),
            "1w": _ohlcv(260, trend=-0.02),
        }
        result = BitcoinAnalyzer().analyze_from_frames(frames)
        assert result.available
        assert result.bias in (MarketBias.BEARISH, MarketBias.STRONG_BEARISH)
        assert result.score < 0


class TestHardVeto:
    def test_bullish_blocks_short(self) -> None:
        snap = MarketRegimeSnapshot(
            available=True,
            bias=MarketBias.STRONG_BULLISH,
            btc=BitcoinAnalysis(True, MarketBias.STRONG_BULLISH, "bullish", 90.0, 100.0),
            eth=EthereumAnalysis(False),
            dominance=DominanceAnalysis(False),
            fear_greed=FearGreedAnalysis(False),
            funding=FundingAnalysis(False),
            open_interest=OpenInterestAnalysis(False),
            liquidations=LiquidationAnalysis(False),
            global_score=90.0,
            captured_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        reason = hard_veto_reason(snap, SignalDirection.STRONG_SHORT, enabled=True)
        assert reason is not None
        assert "bullish" in reason.lower()
        legacy = to_legacy_regime_snapshot(snap)
        assert legacy.available
        assert legacy.regime is MarketRegime.BULLISH
