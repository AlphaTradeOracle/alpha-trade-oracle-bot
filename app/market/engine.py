"""Orchestrates modular market analyzers into a MarketContext."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd

from app.core.time import utc_now
from app.market.analyzers.bitcoin import BitcoinAnalyzer
from app.market.analyzers.dominance import DominanceAnalyzer
from app.market.analyzers.ethereum import EthereumAnalyzer
from app.market.analyzers.fear_greed import FearGreedAnalyzer
from app.market.analyzers.funding import FundingAnalyzer
from app.market.analyzers.liquidations import LiquidationAnalyzer
from app.market.analyzers.open_interest import OpenInterestAnalyzer
from app.market.types import AnalyzerResult, MarketBias, MarketContext, bias_from_signed
from app.signals.regime import MarketRegime, RegimeSnapshot


# Weights inside the global market score (BTC-dominant by design).
COMPONENT_WEIGHTS = {
    "bitcoin": 0.55,
    "ethereum": 0.15,
    "dominance": 0.15,
    "fear_greed": 0.15,
}


class MarketRegimeEngine:
    """Global market filter run before each trade decision."""

    def __init__(
        self,
        *,
        bitcoin: BitcoinAnalyzer | None = None,
        ethereum: EthereumAnalyzer | None = None,
        dominance: DominanceAnalyzer | None = None,
        funding: FundingAnalyzer | None = None,
        fear_greed: FearGreedAnalyzer | None = None,
        open_interest: OpenInterestAnalyzer | None = None,
        liquidations: LiquidationAnalyzer | None = None,
        component_weights: dict[str, float] | None = None,
    ) -> None:
        self._analyzers = [
            bitcoin or BitcoinAnalyzer(),
            ethereum or EthereumAnalyzer(),
            dominance or DominanceAnalyzer(),
            fear_greed or FearGreedAnalyzer(),
            funding or FundingAnalyzer(),
            open_interest or OpenInterestAnalyzer(),
            liquidations or LiquidationAnalyzer(),
        ]
        self._weights = component_weights or COMPONENT_WEIGHTS

    def analyze(
        self,
        *,
        asof: datetime | None = None,
        btc_frames: dict[str, pd.DataFrame] | None = None,
        eth_frames: dict[str, pd.DataFrame] | None = None,
        symbol: str | None = None,
    ) -> MarketContext:
        when = asof or utc_now()
        eth_bundle = dict(eth_frames or {})
        if btc_frames and "4h" in btc_frames:
            eth_bundle.setdefault("btc_4h", btc_frames["4h"])

        frames_for = {
            "bitcoin": btc_frames,
            "ethereum": eth_bundle or None,
            "dominance": None,
            "fear_greed": None,
            "funding": None,
            "open_interest": None,
            "liquidations": None,
        }

        components: dict[str, AnalyzerResult] = {}
        for analyzer in self._analyzers:
            result = analyzer.analyze(
                asof=when,
                frames=frames_for.get(analyzer.name),
                symbol=symbol,
            )
            components[analyzer.name] = result

        market_score, available, detail = self._aggregate(components)
        bias = bias_from_signed(market_score) if available else MarketBias.NEUTRAL
        return MarketContext(
            asof=when,
            bias=bias,
            market_score=round(market_score, 2),
            available=available,
            detail=detail,
            components=components,
        )

    def to_legacy_regime(self, context: MarketContext) -> RegimeSnapshot:
        """Bridge to the existing binary paper/scan regime gate."""
        if not context.available:
            return RegimeSnapshot(None, context.detail or "market_unavailable", False)
        if context.bias in {MarketBias.STRONG_BULLISH, MarketBias.BULLISH}:
            regime = MarketRegime.BULLISH
        elif context.bias in {MarketBias.STRONG_BEARISH, MarketBias.BEARISH}:
            regime = MarketRegime.BEARISH
        else:
            regime = MarketRegime.NEUTRAL
        return RegimeSnapshot(regime, context.detail, True)

    def _aggregate(
        self, components: dict[str, AnalyzerResult]
    ) -> tuple[float, bool, str]:
        weighted = 0.0
        weight_sum = 0.0
        parts: list[str] = []
        for name, weight in self._weights.items():
            result = components.get(name)
            if result is None or not result.available:
                continue
            weighted += result.score * weight
            weight_sum += weight
            parts.append(f"{name}={result.score:+.0f}")

        if weight_sum <= 0:
            # Fall back to bitcoin-only if somehow only funding stubs ran
            btc = components.get("bitcoin")
            if btc and btc.available:
                return btc.score, True, btc.detail
            return 0.0, False, "no_market_components_available"

        score = weighted / weight_sum
        return score, True, " | ".join(parts)


def desk_regime_payload(context: MarketContext) -> dict:
    """Compact shape for the dashboard Market Regime card."""
    btc = context.components.get("bitcoin")
    dom = context.components.get("dominance")
    funding = context.components.get("funding")
    fg = context.components.get("fear_greed")
    tf4 = (btc.metrics.get("timeframes", {}) or {}).get("4h", {}) if btc else {}
    return {
        "asof": context.asof.isoformat(),
        "status": context.bias.value,
        "marketScore": context.market_score,
        "available": context.available,
        "detail": context.detail,
        "btcTrend": tf4.get("trend") if tf4 else None,
        "btcBias": btc.bias.value if btc and btc.available else None,
        "btcScore": btc.score if btc and btc.available else None,
        "btcDominance": (dom.metrics.get("btcDominance") if dom else None),
        "usdtDominance": (dom.metrics.get("usdtDominance") if dom else None),
        "fundingStatus": (
            "configured" if funding and funding.available else "pending_feed"
        ),
        "fearGreed": (fg.metrics.get("label") if fg else None),
    }


def trade_market_context_payload(context: MarketContext) -> dict:
    """Snapshot stored on a trade/signal for the desk Market Context panel."""
    btc = context.components.get("bitcoin")
    dom = context.components.get("dominance")
    funding = context.components.get("funding")
    fg = context.components.get("fear_greed")
    oi = context.components.get("open_interest")
    liq = context.components.get("liquidations")
    # Prefer 4h metrics; fall back to any available TF.
    tf_metrics: dict = {}
    if btc and btc.available:
        tfs = btc.metrics.get("timeframes") or {}
        for key in ("4h", "1d", "1h", "1w"):
            if key in tfs:
                tf_metrics = tfs[key]
                break
        if not tf_metrics and tfs:
            tf_metrics = next(iter(tfs.values()))

    ema_status = None
    if tf_metrics:
        close = tf_metrics.get("close")
        e20, e50, e200 = (
            tf_metrics.get("ema20"),
            tf_metrics.get("ema50"),
            tf_metrics.get("ema200"),
        )
        if close is not None and e20 is not None and e50 is not None:
            above = sum(
                1
                for ema in (e20, e50, e200)
                if ema is not None and close > ema
            )
            below = sum(
                1
                for ema in (e20, e50, e200)
                if ema is not None and close < ema
            )
            if above >= 2:
                ema_status = "above"
            elif below >= 2:
                ema_status = "below"
            else:
                ema_status = "mixed"

    return {
        "asof": context.asof.isoformat(),
        "overallBias": context.bias.value,
        "marketScore": context.market_score,
        "btcPrice": tf_metrics.get("close"),
        "btcBias": btc.bias.value if btc and btc.available else None,
        "btcTrend": tf_metrics.get("trend"),
        "btcRsi": tf_metrics.get("rsi"),
        "btcEmaStatus": ema_status,
        "btcVolatility": tf_metrics.get("atrPercent"),
        "btcDominance": dom.metrics.get("btcDominance") if dom else None,
        "usdtDominance": dom.metrics.get("usdtDominance") if dom else None,
        "fearGreed": fg.metrics.get("label") if fg else None,
        "fundingRate": funding.metrics.get("current") if funding else None,
        "openInterest": oi.metrics.get("oi") if oi else None,
        "liquidations": {
            "long": liq.metrics.get("longLiquidations") if liq else None,
            "short": liq.metrics.get("shortLiquidations") if liq else None,
        },
    }
