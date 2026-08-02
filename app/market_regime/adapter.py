"""Bridge between MarketRegimeSnapshot and the legacy RegimeSnapshot gate."""

from __future__ import annotations

from app.core.enums import SignalDirection
from app.market_regime.types import MarketBias, MarketRegimeSnapshot
from app.signals.regime import MarketRegime, RegimeSnapshot, regime_block_reason


def bias_to_market_regime(bias: MarketBias | None) -> MarketRegime | None:
    if bias is None:
        return None
    if bias in (MarketBias.STRONG_BULLISH, MarketBias.BULLISH):
        return MarketRegime.BULLISH
    if bias in (MarketBias.STRONG_BEARISH, MarketBias.BEARISH):
        return MarketRegime.BEARISH
    return MarketRegime.NEUTRAL


def to_legacy_regime_snapshot(snapshot: MarketRegimeSnapshot | None) -> RegimeSnapshot:
    if snapshot is None or not snapshot.available:
        detail = snapshot.detail if snapshot is not None else "market_regime_unavailable"
        return RegimeSnapshot(None, detail, False)
    regime = bias_to_market_regime(snapshot.bias)
    return RegimeSnapshot(regime, snapshot.detail, True)


def hard_veto_reason(
    snapshot: MarketRegimeSnapshot | None,
    direction: SignalDirection,
    *,
    enabled: bool = True,
    veto_strong_only: bool = False,
) -> str | None:
    """Optional hard block based on aggregated market bias."""
    if not enabled or snapshot is None or not snapshot.available:
        return None
    bias = snapshot.bias
    if veto_strong_only and bias not in (
        MarketBias.STRONG_BULLISH,
        MarketBias.STRONG_BEARISH,
    ):
        return None
    legacy = bias_to_market_regime(bias)
    if legacy is None:
        return None
    reason = regime_block_reason(legacy, direction)
    if reason is None:
        return None
    return f"{reason} (market_bias={bias.value})"
