"""BTC.D / USDT.D / TOTAL3 dominance analyzer."""

from __future__ import annotations

from dataclasses import dataclass

from app.market_regime.sources import DominanceClient, DominanceReading
from app.market_regime.types import DominanceAnalysis, DominanceTrend, RiskMode


@dataclass
class _PrevDominance:
    btc_d: float
    usdt_d: float | None
    alt_mcap: float | None


_PREV: _PrevDominance | None = None


class DominanceAnalyzer:
    def __init__(self, client: DominanceClient | None = None) -> None:
        self._client = client

    async def analyze(self) -> DominanceAnalysis:
        global _PREV
        if self._client is None:
            return DominanceAnalysis(available=False, detail="dominance_client_missing")
        reading = await self._client.fetch()
        if reading is None:
            return DominanceAnalysis(available=False, detail="dominance_unavailable")

        btc_trend = DominanceTrend.FLAT
        usdt_mode = RiskMode.NEUTRAL
        total3_trend = "unknown"
        if _PREV is not None:
            btc_trend = _trend(reading.btc_dominance, _PREV.btc_d, eps=0.15)
            if reading.usdt_dominance is not None and _PREV.usdt_d is not None:
                usdt_delta = reading.usdt_dominance - _PREV.usdt_d
                if usdt_delta > 0.05:
                    usdt_mode = RiskMode.RISK_OFF
                elif usdt_delta < -0.05:
                    usdt_mode = RiskMode.RISK_ON
            if reading.alt_market_cap is not None and _PREV.alt_mcap is not None and _PREV.alt_mcap > 0:
                chg = (reading.alt_market_cap - _PREV.alt_mcap) / _PREV.alt_mcap
                if chg > 0.01:
                    total3_trend = "rising"
                elif chg < -0.01:
                    total3_trend = "falling"
                else:
                    total3_trend = "flat"

        score = _score(btc_trend, usdt_mode, total3_trend, reading)
        _PREV = _PrevDominance(
            btc_d=reading.btc_dominance,
            usdt_d=reading.usdt_dominance,
            alt_mcap=reading.alt_market_cap,
        )
        breadth = "expanding" if total3_trend == "rising" else (
            "contracting" if total3_trend == "falling" else "mixed"
        )
        return DominanceAnalysis(
            available=True,
            btc_dominance=round(reading.btc_dominance, 3),
            btc_dominance_trend=btc_trend,
            usdt_dominance=(
                None if reading.usdt_dominance is None else round(reading.usdt_dominance, 3)
            ),
            usdt_risk_mode=usdt_mode,
            total3_trend=total3_trend,
            total3_breadth=breadth,
            score=score,
            detail=(
                f"btc.d={reading.btc_dominance:.2f}% ({btc_trend.value}) "
                f"usdt.d={reading.usdt_dominance} mode={usdt_mode.value} "
                f"total3={total3_trend}"
            ),
        )


def _trend(current: float, previous: float, *, eps: float) -> DominanceTrend:
    delta = current - previous
    if delta > eps:
        return DominanceTrend.RISING
    if delta < -eps:
        return DominanceTrend.FALLING
    return DominanceTrend.FLAT


def _score(
    btc_trend: DominanceTrend,
    usdt_mode: RiskMode,
    total3_trend: str,
    reading: DominanceReading,
) -> float:
    """Positive = altcoin / risk-on friendly (supports longs on alts)."""
    score = 0.0
    if btc_trend is DominanceTrend.FALLING:
        score += 35.0
    elif btc_trend is DominanceTrend.RISING:
        score -= 35.0
    if usdt_mode is RiskMode.RISK_ON:
        score += 30.0
    elif usdt_mode is RiskMode.RISK_OFF:
        score -= 30.0
    if total3_trend == "rising":
        score += 20.0
    elif total3_trend == "falling":
        score -= 20.0
    # Absolute level soft tilt: very high BTC.D often means alts lag.
    if reading.btc_dominance >= 55:
        score -= 10.0
    elif reading.btc_dominance <= 45:
        score += 10.0
    return round(max(-100.0, min(100.0, score)), 2)
