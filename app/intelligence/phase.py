"""Market Phase classification (KB Part 1)."""

from __future__ import annotations

from app.core.enums import MarketPhase
from app.intelligence.types import InstitutionalPhase, PhaseSnapshot, clamp_score
from app.market_regime.types import MarketBias, MarketRegimeSnapshot


_PHASE_BEHAVIOUR: dict[InstitutionalPhase, str] = {
    InstitutionalPhase.TRENDING_BULLISH: "Long setups preferred; counter-trend scored lower.",
    InstitutionalPhase.TRENDING_BEARISH: "Short setups preferred; counter-trend longs scored lower.",
    InstitutionalPhase.RANGE: "Mean reversion preferred; breakouts need stronger confirmation.",
    InstitutionalPhase.EXPANSION: "Momentum continuation preferred; higher volatility expected.",
    InstitutionalPhase.COMPRESSION: "Breakout probability rises; do not chase candles.",
    InstitutionalPhase.ACCUMULATION: "Monitor bullish reversals and liquidity sweeps.",
    InstitutionalPhase.DISTRIBUTION: "Monitor bearish reversals and exhaustion.",
    InstitutionalPhase.RECOVERY: "Cautious long bias after capitulation; confirm structure.",
    InstitutionalPhase.CAPITULATION: "Forced selling; wait for structure + liquidity reclaim.",
    InstitutionalPhase.HIGH_VOLATILITY: "Reduce size; widen stops; demand more confluence.",
    InstitutionalPhase.LOW_VOLATILITY: "Avoid forcing trades; expect fake breakouts.",
    InstitutionalPhase.UNCERTAIN: "No directional preference; raise confirmation bar.",
}


def classify_market_phase(regime: MarketRegimeSnapshot | None) -> PhaseSnapshot:
    """Derive institutional phase from global regime snapshot."""
    if regime is None or not regime.available:
        return PhaseSnapshot(
            phase=InstitutionalPhase.UNCERTAIN,
            confidence=35.0,
            strength=0.0,
            expected_behaviour=_PHASE_BEHAVIOUR[InstitutionalPhase.UNCERTAIN],
            legacy_phase=None,
        )

    bias = regime.bias
    btc_tf = regime.btc.timeframes.get("4h") or next(
        iter(regime.btc.timeframes.values()), None
    )
    atr_pct = float(btc_tf.atr_percent) if btc_tf and btc_tf.atr_percent is not None else None
    vol = float(btc_tf.volatility) if btc_tf and btc_tf.volatility is not None else None
    structure = btc_tf.structure if btc_tf else None

    phase = InstitutionalPhase.UNCERTAIN
    strength = abs(bias.score)
    confidence = 55.0 + min(30.0, strength * 0.25)

    high_vol = (atr_pct is not None and atr_pct >= 4.0) or (vol is not None and vol >= 0.04)
    low_vol = (atr_pct is not None and atr_pct <= 1.0) or (vol is not None and vol <= 0.01)

    if high_vol:
        phase = InstitutionalPhase.HIGH_VOLATILITY
        confidence = clamp_score(confidence - 5.0)
    elif low_vol:
        if bias is MarketBias.NEUTRAL:
            phase = InstitutionalPhase.LOW_VOLATILITY
        else:
            phase = InstitutionalPhase.COMPRESSION
    elif bias is MarketBias.STRONG_BULLISH:
        phase = InstitutionalPhase.TRENDING_BULLISH
        if structure and structure.choch_bearish:
            phase = InstitutionalPhase.DISTRIBUTION
            confidence -= 10.0
    elif bias is MarketBias.BULLISH:
        phase = InstitutionalPhase.TRENDING_BULLISH
        if structure and structure.fvg_bullish and not structure.bos_bullish:
            phase = InstitutionalPhase.ACCUMULATION
    elif bias is MarketBias.STRONG_BEARISH:
        phase = InstitutionalPhase.TRENDING_BEARISH
        if structure and structure.choch_bullish:
            phase = InstitutionalPhase.RECOVERY
            confidence -= 10.0
        elif atr_pct is not None and atr_pct >= 3.5:
            phase = InstitutionalPhase.CAPITULATION
    elif bias is MarketBias.BEARISH:
        phase = InstitutionalPhase.TRENDING_BEARISH
        if structure and structure.fvg_bearish and not structure.bos_bearish:
            phase = InstitutionalPhase.DISTRIBUTION
    else:
        if structure and (structure.higher_highs or structure.lower_lows):
            phase = InstitutionalPhase.EXPANSION
        else:
            phase = InstitutionalPhase.RANGE

    legacy = _to_legacy(phase)
    return PhaseSnapshot(
        phase=phase,
        confidence=clamp_score(confidence),
        strength=clamp_score(strength),
        expected_behaviour=_PHASE_BEHAVIOUR[phase],
        legacy_phase=legacy,
    )


def _to_legacy(phase: InstitutionalPhase) -> MarketPhase:
    if phase in (
        InstitutionalPhase.TRENDING_BULLISH,
        InstitutionalPhase.ACCUMULATION,
        InstitutionalPhase.RECOVERY,
    ):
        return MarketPhase.UPTREND
    if phase in (
        InstitutionalPhase.TRENDING_BEARISH,
        InstitutionalPhase.DISTRIBUTION,
        InstitutionalPhase.CAPITULATION,
    ):
        return MarketPhase.DOWNTREND
    if phase in (InstitutionalPhase.HIGH_VOLATILITY, InstitutionalPhase.EXPANSION):
        return MarketPhase.VOLATILE
    return MarketPhase.RANGE
