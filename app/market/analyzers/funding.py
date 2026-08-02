"""Per-coin + BTC funding-rate analyzer (Binance USD-M)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.market.types import AnalyzerResult, MarketBias, bias_from_signed

#: ~0.01% = 0.0001 typical; treat |rate| >= 0.05% as elevated, >= 0.1% extreme.
ELEVATED = 0.0005
EXTREME = 0.0010


def score_from_funding(rate: float) -> float:
    """Positive funding (longs pay) → crowded longs → negative (risk-off) lean."""
    if rate >= EXTREME:
        return -70.0
    if rate >= ELEVATED:
        return -35.0
    if rate <= -EXTREME:
        return 70.0
    if rate <= -ELEVATED:
        return 35.0
    # Linear band for normal funding
    return max(-30.0, min(30.0, -rate * 60_000.0))


def _extreme_label(rate: float) -> str | None:
    if abs(rate) >= EXTREME:
        return "extreme_positive" if rate > 0 else "extreme_negative"
    if abs(rate) >= ELEVATED:
        return "elevated_positive" if rate > 0 else "elevated_negative"
    return None


class FundingAnalyzer:
    name = "funding"

    def analyze(
        self,
        *,
        asof: datetime,
        frames: dict[str, Any] | None = None,
        symbol: str | None = None,
    ) -> AnalyzerResult:
        del asof
        payload = None
        btc_payload = None
        if isinstance(frames, dict):
            payload = frames.get("payload") or frames.get("coin_funding")
            btc_payload = frames.get("btc") or frames.get("btc_funding")

        if not payload and not btc_payload:
            return AnalyzerResult(
                name=self.name,
                available=False,
                score=0.0,
                bias=MarketBias.NEUTRAL,
                detail=f"funding_unavailable symbol={symbol or 'n/a'}",
                metrics={
                    "symbol": symbol,
                    "current": None,
                    "average": None,
                    "changeHours": None,
                    "extreme": None,
                    "btcFunding": None,
                },
            )

        coin_rate = float(payload["current"]) if payload and payload.get("current") is not None else None
        btc_rate = (
            float(btc_payload["current"])
            if btc_payload and btc_payload.get("current") is not None
            else None
        )
        # Prefer coin funding; blend lightly with BTC when both present.
        if coin_rate is not None and btc_rate is not None:
            score = 0.7 * score_from_funding(coin_rate) + 0.3 * score_from_funding(btc_rate)
            primary = coin_rate
        elif coin_rate is not None:
            score = score_from_funding(coin_rate)
            primary = coin_rate
        else:
            score = score_from_funding(btc_rate or 0.0)
            primary = btc_rate or 0.0

        average = payload.get("average") if payload else None
        change = payload.get("changeHours") if payload else None
        return AnalyzerResult(
            name=self.name,
            available=True,
            score=round(max(-100.0, min(100.0, score)), 2),
            bias=bias_from_signed(score),
            detail=(
                f"funding coin={primary:.6f}"
                + (f" btc={btc_rate:.6f}" if btc_rate is not None else "")
                + f" lean={score:+.0f}"
            ),
            metrics={
                "symbol": (payload or {}).get("symbol") or symbol,
                "current": primary,
                "average": average,
                "changeHours": change,
                "extreme": _extreme_label(primary),
                "btcFunding": btc_rate,
            },
        )
