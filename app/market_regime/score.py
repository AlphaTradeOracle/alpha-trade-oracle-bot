"""Final score blend: coin analysis + global market factors."""

from __future__ import annotations

from app.core.enums import SignalDirection
from app.market_regime.types import (
    BlendedScoreResult,
    MarketRegimeSnapshot,
    ScoreWeights,
)


class FinalScoreCalculator:
    """Combine coin score with directional market-context scores."""

    def __init__(self, weights: ScoreWeights | None = None) -> None:
        self._weights = weights or ScoreWeights()

    @property
    def weights(self) -> ScoreWeights:
        return self._weights

    def blend(
        self,
        coin_score: float,
        direction: SignalDirection,
        snapshot: MarketRegimeSnapshot | None,
    ) -> BlendedScoreResult:
        """Return a 0..100 final score aligned with existing SignalEngine scale.

        Market factor scores are in [-100, +100] (bullish positive). For shorts
        they are sign-flipped before blending so a bullish market reduces short
        quality and a bearish market improves it.
        """
        coin = max(0.0, min(100.0, float(coin_score)))
        if snapshot is None or not snapshot.available:
            return BlendedScoreResult(
                final_score=round(coin, 2),
                coin_score=round(coin, 2),
                global_score=0.0,
                funding_score=0.0,
                oi_score=0.0,
                liquidation_score=0.0,
                weights_used={"coin": 1.0, "global": 0.0, "funding": 0.0, "open_interest": 0.0, "liquidations": 0.0},
                detail="market_context_unavailable_coin_only",
            )

        if not direction.is_long and not direction.is_short:
            return BlendedScoreResult(
                final_score=round(coin, 2),
                coin_score=round(coin, 2),
                global_score=snapshot.global_score,
                funding_score=snapshot.funding.score if snapshot.funding.available else 0.0,
                oi_score=snapshot.open_interest.score if snapshot.open_interest.available else 0.0,
                liquidation_score=snapshot.liquidations.score if snapshot.liquidations.available else 0.0,
                weights_used={"coin": 1.0, "global": 0.0, "funding": 0.0, "open_interest": 0.0, "liquidations": 0.0},
                detail="non_actionable_direction_coin_only",
            )

        global_raw = snapshot.global_score
        funding_raw = snapshot.funding.score if snapshot.funding.available else 0.0
        oi_raw = snapshot.open_interest.score if snapshot.open_interest.available else 0.0
        liq_raw = snapshot.liquidations.score if snapshot.liquidations.available else 0.0

        weights = self._weights.normalized(
            has_global=True,
            has_funding=snapshot.funding.available,
            has_oi=snapshot.open_interest.available,
            has_liquidations=snapshot.liquidations.available,
        )

        # Market factors are bullish-positive in [-100, +100].
        # Longs: higher side contribution = better. Shorts use the inverted
        # engine scale (low score = strong short), so we blend in short-quality
        # space and map back — otherwise a bearish market would push short
        # scores above SIGNAL_SHORT_MAX_SCORE and reject good shorts.
        def side_quality(raw: float, *, for_short: bool) -> float:
            aligned = (-raw) if for_short else raw
            return max(0.0, min(100.0, 50.0 + 0.5 * aligned))

        for_short = direction.is_short
        coin_q = (100.0 - coin) if for_short else coin
        final_q = (
            weights["coin"] * coin_q
            + weights["global"] * side_quality(global_raw, for_short=for_short)
            + weights["funding"] * side_quality(funding_raw, for_short=for_short)
            + weights["open_interest"] * side_quality(oi_raw, for_short=for_short)
            + weights["liquidations"] * side_quality(liq_raw, for_short=for_short)
        )
        final = (100.0 - final_q) if for_short else final_q
        return BlendedScoreResult(
            final_score=round(max(0.0, min(100.0, final)), 2),
            coin_score=round(coin, 2),
            global_score=round(global_raw, 2),
            funding_score=round(funding_raw, 2),
            oi_score=round(oi_raw, 2),
            liquidation_score=round(liq_raw, 2),
            weights_used=weights,
            detail=(
                f"blend coin={coin:.1f} global={global_raw:.1f} "
                f"funding={funding_raw:.1f} oi={oi_raw:.1f} liq={liq_raw:.1f} "
                f"dir={direction.value} short_remap={for_short}"
            ),
        )
