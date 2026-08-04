"""Tests fuer BTC-Regime-Filter und Short-Erschoepfungs-Gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.core.enums import SignalDirection, SuppressionReason
from app.indicators.engine import IndicatorSet
from app.signals.dedup import SignalDeduplicator
from app.signals.engine import SignalEngine, SignalEngineConfig
from app.signals.regime import (
    MarketRegime,
    RegimeSnapshot,
    direction_allowed_by_regime,
    regime_block_reason,
    regime_from_indicators,
)
from tests.test_dedup import make_result

NOW = datetime(2024, 6, 1, 12, tzinfo=UTC)


def _btc_indicators(
    *,
    close: float = 100.0,
    ema20: float = 95.0,
    ema50: float = 90.0,
    st_dir: int = 1,
) -> IndicatorSet:
    return IndicatorSet(
        timeframe="4h",
        candle_open_time=NOW,
        close_price=close,
        ema_20=ema20,
        ema_50=ema50,
        supertrend_direction=st_dir,
    )


class TestRegimeFromIndicators:
    def test_bullish_when_close_above_emas_and_stacked(self) -> None:
        snap = regime_from_indicators(_btc_indicators(close=110, ema20=100, ema50=95))
        assert snap.available
        assert snap.regime is MarketRegime.BULLISH

    def test_bearish_when_close_below_emas(self) -> None:
        snap = regime_from_indicators(_btc_indicators(close=80, ema20=95, ema50=100, st_dir=-1))
        assert snap.available
        assert snap.regime is MarketRegime.BEARISH

    def test_degrades_when_ema_missing(self) -> None:
        indicators = replace(_btc_indicators(), ema_20=None)
        snap = regime_from_indicators(indicators)
        assert not snap.available
        assert snap.regime is None


class TestRegimeDirectionGate:
    def test_bullish_blocks_shorts(self) -> None:
        assert not direction_allowed_by_regime(MarketRegime.BULLISH, SignalDirection.STRONG_SHORT)
        assert direction_allowed_by_regime(MarketRegime.BULLISH, SignalDirection.STRONG_LONG)

    def test_bearish_blocks_longs(self) -> None:
        assert not direction_allowed_by_regime(MarketRegime.BEARISH, SignalDirection.STRONG_LONG)
        assert direction_allowed_by_regime(MarketRegime.BEARISH, SignalDirection.STRONG_SHORT)

    def test_unknown_regime_allows_all(self) -> None:
        assert direction_allowed_by_regime(None, SignalDirection.STRONG_SHORT)


class TestEngineRegimeAndExhaustion:
    def test_bullish_regime_blocks_short(
        self, downtrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        modified = dict(downtrend_indicators)
        modified["1h"] = replace(
            downtrend_indicators["1h"],
            rsi_14=40.0,
            adx_14=30.0,
        )
        engine = SignalEngine(SignalEngineConfig(regime_filter_enabled=True))
        result = engine.generate(
            "ETHUSDT",
            modified,
            now=NOW,
            market_regime=MarketRegime.BULLISH,
        )
        if result.direction.is_short or result.direction is SignalDirection.NO_TRADE:
            assert result.direction is SignalDirection.NO_TRADE
            assert result.no_trade_reason is not None
            assert "bullish" in result.no_trade_reason.lower()

    def test_short_exhaustion_score_blocks_trade(
        self, downtrend_indicators: dict[str, IndicatorSet]
    ) -> None:
        engine = SignalEngine(SignalEngineConfig(short_min_score=18.0))
        modified = dict(downtrend_indicators)
        modified["1h"] = replace(
            downtrend_indicators["1h"],
            rsi_14=40.0,
            adx_14=30.0,
        )
        result = engine.generate("ETHUSDT", modified, now=NOW)
        if result.score <= 18.0 and result.direction.is_short:
            assert result.direction is SignalDirection.NO_TRADE
            assert "Erschoepfungsband" in (result.no_trade_reason or "")


class TestDedupShortExhaustion:
    @pytest.mark.asyncio
    async def test_rejects_short_at_exhaustion_score(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(
                direction=SignalDirection.STRONG_SHORT,
                score=18.0,
                fingerprint="short-exhaust",
            ),
            min_score=75.0,
            short_max_score=25.0,
            short_min_score=18.0,
            min_risk_reward_ratio=2.0,
            require_strong=True,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.SHORT_EXHAUSTION

    @pytest.mark.asyncio
    async def test_allows_short_above_exhaustion_band(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(
                direction=SignalDirection.STRONG_SHORT,
                score=22.0,
                fingerprint="short-ok",
            ),
            min_score=75.0,
            short_max_score=25.0,
            short_min_score=18.0,
            min_risk_reward_ratio=2.0,
            require_strong=True,
            now=NOW,
        )
        assert decision.should_send is True

    @pytest.mark.asyncio
    async def test_regime_filter_suppresses_mismatch(self) -> None:
        decision = await SignalDeduplicator().evaluate(
            make_result(
                direction=SignalDirection.STRONG_SHORT,
                score=22.0,
                fingerprint="short-regime",
            ),
            min_score=75.0,
            short_max_score=25.0,
            short_min_score=18.0,
            min_risk_reward_ratio=2.0,
            require_strong=True,
            market_regime=MarketRegime.BULLISH,
            regime_filter_enabled=True,
            now=NOW,
        )
        assert decision.should_send is False
        assert decision.reason is SuppressionReason.REGIME_FILTER


class TestConfigDefaults:
    def test_new_strategy_defaults(self) -> None:
        settings = Settings()
        assert settings.regime_filter_enabled is True
        assert settings.signal_rsi_short_min == 33.0
        assert settings.signal_short_min_score == 18.0
        assert settings.paper_retest_zone_near == 0.40
        assert settings.paper_retest_zone_far == 1.15
        assert settings.atr_multiplier == 1.8
        assert settings.paper_retest_pending_multiplier == 6
        assert settings.paper_retest_min_bars_in_zone == 1
        assert settings.paper_early_scratch_hours == 12
        assert settings.paper_early_scratch_mfe_r == 0.5


class TestRegimeBlockReason:
    def test_reason_for_bullish_short(self) -> None:
        reason = regime_block_reason(MarketRegime.BULLISH, SignalDirection.STRONG_SHORT)
        assert reason is not None
        assert "bullish" in reason.lower()
