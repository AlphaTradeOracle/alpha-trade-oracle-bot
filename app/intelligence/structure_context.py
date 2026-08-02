"""Market Structure & Liquidity context (KB Part 3)."""

from __future__ import annotations

from app.intelligence.types import StructureContextSnapshot, clamp_score
from app.market_regime.types import MarketRegimeSnapshot


def build_structure_context(regime: MarketRegimeSnapshot | None) -> StructureContextSnapshot:
    if regime is None or not regime.available or not regime.btc.available:
        return StructureContextSnapshot(
            trend="unknown",
            structure_label="unavailable",
            bos=None,
            choch=None,
            liquidity_status="unknown",
            volume_confirmed=False,
            structure_score=0.0,
            confidence=25.0,
            reasons=("BTC structure unavailable before coin analysis.",),
        )

    tf = regime.btc.timeframes.get("4h") or next(iter(regime.btc.timeframes.values()), None)
    structure = tf.structure if tf else None
    reasons: list[str] = [f"BTC bias {regime.btc.bias.label}; trend={regime.btc.trend}."]

    if structure is None:
        return StructureContextSnapshot(
            trend=regime.btc.trend,
            structure_label="incomplete",
            bos=None,
            choch=None,
            liquidity_status="unknown",
            volume_confirmed=False,
            structure_score=40.0,
            confidence=40.0,
            reasons=tuple(reasons + ["No structure snapshot on preferred TF."]),
        )

    label_parts: list[str] = []
    if structure.higher_highs and structure.higher_lows:
        label_parts.append("HH_HL")
    elif structure.lower_highs and structure.lower_lows:
        label_parts.append("LH_LL")
    else:
        label_parts.append("RANGE")

    bos = None
    if structure.bos_bullish:
        bos = "bullish"
        label_parts.append("BOS+")
    elif structure.bos_bearish:
        bos = "bearish"
        label_parts.append("BOS-")

    choch = None
    if structure.choch_bullish:
        choch = "bullish"
        label_parts.append("CHoCH+")
    elif structure.choch_bearish:
        choch = "bearish"
        label_parts.append("CHoCH-")

    if structure.fvg_bullish:
        reasons.append("Bullish FVG present.")
    if structure.fvg_bearish:
        reasons.append("Bearish FVG present.")
    if structure.order_block_high is not None:
        reasons.append("Order-block zone detected.")

    liq_score = regime.liquidations.liquidity_score
    if liq_score is None:
        liquidity_status = "unconfirmed"
    elif liq_score >= 70:
        liquidity_status = "abundant"
    elif liq_score >= 45:
        liquidity_status = "adequate"
    else:
        liquidity_status = "thin"
        reasons.append("Liquidity thin — structure confidence reduced.")

    volume_confirmed = bool(tf and tf.volume_ratio is not None and tf.volume_ratio >= 1.0)
    if volume_confirmed:
        reasons.append("Volume confirms structural move.")
    else:
        reasons.append("Volume confirmation weak or missing.")

    score = 50.0
    if "HH_HL" in label_parts or "LH_LL" in label_parts:
        score += 15.0
    if bos:
        score += 10.0
    if choch:
        score += 8.0
    if volume_confirmed:
        score += 8.0
    if liquidity_status == "thin":
        score -= 12.0
    elif liquidity_status == "abundant":
        score += 6.0

    confidence = 45.0 + (10.0 if volume_confirmed else 0.0)
    if liquidity_status in ("adequate", "abundant"):
        confidence += 10.0
    if bos or choch:
        confidence += 8.0

    reasons.extend(list(structure.notes)[:3])

    return StructureContextSnapshot(
        trend=regime.btc.trend,
        structure_label="+".join(label_parts),
        bos=bos,
        choch=choch,
        liquidity_status=liquidity_status,
        volume_confirmed=volume_confirmed,
        structure_score=clamp_score(score),
        confidence=clamp_score(confidence),
        reasons=tuple(reasons),
    )
