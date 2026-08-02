"""Data Quality & Reliability Engine (KB Part 7)."""

from __future__ import annotations

from app.intelligence.types import DataQualitySnapshot, clamp_score
from app.market_regime.types import MarketRegimeSnapshot


def evaluate_data_quality(
    *,
    candle_data_quality: float,
    regime: MarketRegimeSnapshot | None,
    exchange_ok: bool = True,
    min_quality: float = 70.0,
) -> DataQualitySnapshot:
    warnings: list[str] = []
    errors: list[str] = []
    unavailable: list[str] = []

    if not exchange_ok:
        errors.append("Exchange data unavailable")
        unavailable.append("exchange")

    quality = clamp_score(candle_data_quality)
    reliability = 80.0

    if regime is None or not regime.available:
        unavailable.append("market_regime")
        warnings.append("Market regime unavailable — confidence reduced.")
        quality = min(quality, 65.0)
        reliability -= 15.0
    else:
        if not regime.btc.available:
            unavailable.append("bitcoin")
            errors.append("Bitcoin status missing (mandatory)")
            quality = min(quality, 55.0)
        if not regime.eth.available:
            unavailable.append("ethereum")
            warnings.append("Ethereum analysis optional/unavailable.")
            reliability -= 3.0
        if not regime.funding.available:
            unavailable.append("funding")
            warnings.append("Funding optional/unavailable.")
            reliability -= 4.0
        if not regime.open_interest.available:
            unavailable.append("open_interest")
            warnings.append("Open interest optional/unavailable.")
            reliability -= 4.0
        if not regime.fear_greed.available:
            unavailable.append("fear_greed")
            warnings.append("Fear & Greed optional/unavailable.")
            reliability -= 2.0
        if not regime.dominance.available:
            unavailable.append("dominance")
            warnings.append("Dominance optional/unavailable.")
            reliability -= 3.0
        if not regime.liquidations.available:
            unavailable.append("liquidations")
            warnings.append("Liquidation/liquidity feed optional/unavailable.")
            reliability -= 5.0

    if quality < 70:
        warnings.append(f"Candle data quality {quality:.1f} below institutional target 70.")
    if quality < min_quality:
        errors.append(f"Data quality below configured minimum {min_quality:.1f}.")

    reliability = clamp_score(reliability)
    # Confidence adjustment: how much to subtract from confidence %
    adjustment = 0.0
    if quality < 90:
        adjustment -= (90.0 - quality) * 0.25
    if reliability < 75:
        adjustment -= (75.0 - reliability) * 0.2
    adjustment = max(-35.0, min(0.0, adjustment))

    trade_restricted = bool(errors) or quality < min_quality or "bitcoin" in unavailable

    return DataQualitySnapshot(
        quality_score=quality,
        reliability_score=reliability,
        warnings=tuple(warnings),
        errors=tuple(errors),
        unavailable=tuple(unavailable),
        confidence_adjustment=round(adjustment, 2),
        trade_restricted=trade_restricted,
    )
