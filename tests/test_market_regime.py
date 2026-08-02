"""Tests for the global Market Regime Filter stack."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.enums import SignalDirection
from app.market.analyzers.bitcoin import BitcoinAnalyzer
from app.market.engine import MarketRegimeEngine, desk_regime_payload, trade_market_context_payload
from app.market.final_score import FinalScoreCalculator, signed_to_0_100
from app.market.types import (
    AnalyzerResult,
    MarketBias,
    MarketContext,
    ScoreBlendWeights,
    bias_from_signed,
)
from app.signals.engine import SignalEngine, SignalEngineConfig

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


class TestBiasHelpers:
    def test_bias_from_signed_buckets(self) -> None:
        assert bias_from_signed(80) is MarketBias.STRONG_BULLISH
        assert bias_from_signed(30) is MarketBias.BULLISH
        assert bias_from_signed(0) is MarketBias.NEUTRAL
        assert bias_from_signed(-30) is MarketBias.BEARISH
        assert bias_from_signed(-80) is MarketBias.STRONG_BEARISH

    def test_signed_to_0_100(self) -> None:
        assert signed_to_0_100(0) == 50.0
        assert signed_to_0_100(100) == 100.0
        assert signed_to_0_100(-100) == 0.0


class TestFinalScoreCalculator:
    def _context(self, market_score: float) -> MarketContext:
        return MarketContext(
            asof=NOW,
            bias=bias_from_signed(market_score),
            market_score=market_score,
            available=True,
            detail="test",
            components={
                "bitcoin": AnalyzerResult(
                    name="bitcoin",
                    available=True,
                    score=market_score,
                    bias=bias_from_signed(market_score),
                )
            },
        )

    def test_bullish_market_raises_score(self) -> None:
        calc = FinalScoreCalculator(
            ScoreBlendWeights(coin=0.6, market=0.25, funding=0, open_interest=0, liquidations=0)
        )
        blended = calc.blend(60.0, market=self._context(80.0))
        assert blended.final_score > 60.0
        assert blended.market_component == pytest.approx(90.0)

    def test_bearish_market_lowers_score(self) -> None:
        calc = FinalScoreCalculator(
            ScoreBlendWeights(coin=0.6, market=0.25, funding=0, open_interest=0, liquidations=0)
        )
        blended = calc.blend(70.0, market=self._context(-80.0))
        assert blended.final_score < 70.0

    def test_unavailable_market_keeps_coin_score(self) -> None:
        calc = FinalScoreCalculator()
        ctx = MarketContext(
            asof=NOW,
            bias=MarketBias.NEUTRAL,
            market_score=0.0,
            available=False,
            detail="missing",
        )
        blended = calc.blend(72.0, market=ctx)
        assert blended.final_score == pytest.approx(72.0)


class TestBitcoinAnalyzer:
    def test_uptrend_frames_are_bullish(self, uptrend_df) -> None:
        analyzer = BitcoinAnalyzer(timeframes=("1h", "4h", "1d"))
        frames = {"1h": uptrend_df, "4h": uptrend_df, "1d": uptrend_df}
        result = analyzer.analyze(asof=NOW, frames=frames)
        assert result.available
        assert result.score > 0
        assert result.bias in {MarketBias.BULLISH, MarketBias.STRONG_BULLISH}

    def test_downtrend_frames_are_bearish(self, downtrend_df) -> None:
        analyzer = BitcoinAnalyzer(timeframes=("1h", "4h", "1d"))
        frames = {"1h": downtrend_df, "4h": downtrend_df, "1d": downtrend_df}
        result = analyzer.analyze(asof=NOW, frames=frames)
        assert result.available
        assert result.score < 0
        assert result.bias in {MarketBias.BEARISH, MarketBias.STRONG_BEARISH}

    def test_missing_frames_unavailable(self) -> None:
        result = BitcoinAnalyzer().analyze(asof=NOW, frames=None)
        assert not result.available


class TestEthereumAnalyzer:
    def test_relative_strength_with_btc_frame(self, uptrend_df, downtrend_df) -> None:
        from app.market.analyzers.ethereum import EthereumAnalyzer

        result = EthereumAnalyzer().analyze(
            asof=NOW,
            frames={"4h": uptrend_df, "btc_4h": downtrend_df},
        )
        assert result.available
        assert result.metrics.get("relativeStrengthVsBtc20") is not None


class TestMarketRegimeEngine:
    def test_aggregates_btc_and_stubs(self, uptrend_df) -> None:
        engine = MarketRegimeEngine()
        ctx = engine.analyze(btc_frames={"1h": uptrend_df, "4h": uptrend_df, "1d": uptrend_df})
        assert ctx.available
        assert "bitcoin" in ctx.components
        assert ctx.components["funding"].available is False
        payload = desk_regime_payload(ctx)
        assert payload["status"] == ctx.bias.value
        trade_ctx = trade_market_context_payload(ctx)
        assert "btcBias" in trade_ctx

    def test_legacy_regime_bridge(self, downtrend_df) -> None:
        engine = MarketRegimeEngine()
        ctx = engine.analyze(
            btc_frames={"1h": downtrend_df, "4h": downtrend_df, "1d": downtrend_df}
        )
        snap = engine.to_legacy_regime(ctx)
        assert snap.available
        assert snap.regime is not None
        assert snap.regime.value == "bearish"


class TestSignalEngineMarketBlend:
    def test_blend_changes_score(
        self, uptrend_indicators, downtrend_df
    ) -> None:
        market = MarketRegimeEngine().analyze(
            btc_frames={"1h": downtrend_df, "4h": downtrend_df, "1d": downtrend_df}
        )
        base = SignalEngine(
            SignalEngineConfig(market_regime_score_enabled=False)
        ).generate("ETHUSDT", uptrend_indicators, now=NOW)
        blended = SignalEngine(
            SignalEngineConfig(
                market_regime_score_enabled=True,
                market_score_coin_weight=0.6,
                market_score_market_weight=0.4,
                market_score_funding_weight=0.0,
                market_score_open_interest_weight=0.0,
                market_score_liquidations_weight=0.0,
            )
        ).generate(
            "ETHUSDT",
            uptrend_indicators,
            now=NOW,
            market_context=market,
        )
        assert blended.coin_score == pytest.approx(base.score)
        assert blended.market_context is not None
        # Bearish BTC should pull a long-biased uptrend score down.
        if base.direction.is_long:
            assert blended.score < base.score
