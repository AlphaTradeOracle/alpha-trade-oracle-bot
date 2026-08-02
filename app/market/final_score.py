"""Blend coin score with modular market-context lean (bipolar 0..100)."""

from __future__ import annotations

from app.market.types import BlendedScore, MarketBias, MarketContext, ScoreBlendWeights, bias_from_signed


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def signed_to_0_100(signed: float) -> float:
    """Map [-100, +100] → [0, 100] with 50 = neutral."""
    return _clamp_score(50.0 + signed / 2.0)


class FinalScoreCalculator:
    """Modular weighted blend — weights fully configurable.

    All market modules contribute a *signed* lean (positive = bullish / risk-on).
    Components are mapped onto the same bipolar 0..100 scale as the coin score
    (high = long bias, low = short bias). That way a bullish market raises the
    final score (helps longs, hurts shorts) and a bearish market lowers it.
    """

    def __init__(self, weights: ScoreBlendWeights | None = None) -> None:
        self._weights = (weights or ScoreBlendWeights()).normalized()

    @property
    def weights(self) -> ScoreBlendWeights:
        return self._weights

    def blend(
        self,
        coin_score: float,
        *,
        market: MarketContext | None = None,
        weights: ScoreBlendWeights | None = None,
    ) -> BlendedScore:
        w = (weights or self._weights).normalized()
        coin = _clamp_score(coin_score)

        market_signed = market.market_score if market and market.available else 0.0
        market_comp = signed_to_0_100(market_signed)

        funding = market.components.get("funding") if market else None
        oi = market.components.get("open_interest") if market else None
        liq = market.components.get("liquidations") if market else None

        funding_comp = (
            signed_to_0_100(funding.score) if funding and funding.available else 50.0
        )
        oi_comp = signed_to_0_100(oi.score) if oi and oi.available else 50.0
        liq_comp = signed_to_0_100(liq.score) if liq and liq.available else 50.0

        # Redistribute weight of unavailable non-coin modules onto coin.
        active_market_w = w.market if market and market.available else 0.0
        active_funding_w = w.funding if funding and funding.available else 0.0
        active_oi_w = w.open_interest if oi and oi.available else 0.0
        active_liq_w = w.liquidations if liq and liq.available else 0.0
        active_sum = active_market_w + active_funding_w + active_oi_w + active_liq_w
        coin_w = 1.0 - active_sum
        if active_sum > 0 and coin_w < w.coin:
            coin_w = w.coin
            scale = (1.0 - coin_w) / active_sum
            active_market_w *= scale
            active_funding_w *= scale
            active_oi_w *= scale
            active_liq_w *= scale

        final = (
            coin * coin_w
            + market_comp * active_market_w
            + funding_comp * active_funding_w
            + oi_comp * active_oi_w
            + liq_comp * active_liq_w
        )
        return BlendedScore(
            coin_score=round(coin, 2),
            market_component=round(market_comp, 2),
            funding_component=round(funding_comp, 2),
            open_interest_component=round(oi_comp, 2),
            liquidation_component=round(liq_comp, 2),
            final_score=round(_clamp_score(final), 2),
            overall_bias=bias_from_signed(market_signed) if market and market.available else MarketBias.NEUTRAL,
            weights=ScoreBlendWeights(
                coin=coin_w,
                market=active_market_w,
                funding=active_funding_w,
                open_interest=active_oi_w,
                liquidations=active_liq_w,
            ),
        )
