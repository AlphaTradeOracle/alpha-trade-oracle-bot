"""Shared types for the global Market Regime Filter."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MarketBias(StrEnum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"

    @property
    def label(self) -> str:
        return {
            MarketBias.STRONG_BULLISH: "Strong Bullish",
            MarketBias.BULLISH: "Bullish",
            MarketBias.NEUTRAL: "Neutral",
            MarketBias.BEARISH: "Bearish",
            MarketBias.STRONG_BEARISH: "Strong Bearish",
        }[self]

    @property
    def score(self) -> float:
        """Directional score in [-100, +100]."""
        return {
            MarketBias.STRONG_BULLISH: 90.0,
            MarketBias.BULLISH: 55.0,
            MarketBias.NEUTRAL: 0.0,
            MarketBias.BEARISH: -55.0,
            MarketBias.STRONG_BEARISH: -90.0,
        }[self]


class DominanceTrend(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    FLAT = "flat"


class RiskMode(StrEnum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"


class FearGreedBand(StrEnum):
    EXTREME_FEAR = "extreme_fear"
    FEAR = "fear"
    NEUTRAL = "neutral"
    GREED = "greed"
    EXTREME_GREED = "extreme_greed"


class FundingStatus(StrEnum):
    VERY_POSITIVE = "very_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    VERY_NEGATIVE = "very_negative"


class OiPriceRelation(StrEnum):
    PRICE_UP_OI_UP = "price_up_oi_up"
    PRICE_UP_OI_DOWN = "price_up_oi_down"
    PRICE_DOWN_OI_UP = "price_down_oi_up"
    PRICE_DOWN_OI_DOWN = "price_down_oi_down"
    FLAT = "flat"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StructureSnapshot:
    """Heuristic SMC / market-structure summary for one timeframe."""

    higher_highs: bool = False
    higher_lows: bool = False
    lower_highs: bool = False
    lower_lows: bool = False
    support: float | None = None
    resistance: float | None = None
    liquidity_high: float | None = None
    liquidity_low: float | None = None
    order_block_high: float | None = None
    order_block_low: float | None = None
    fvg_bullish: bool = False
    fvg_bearish: bool = False
    bos_bullish: bool = False
    bos_bearish: bool = False
    choch_bullish: bool = False
    choch_bearish: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TimeframeBiasSnapshot:
    timeframe: str
    bias: MarketBias
    trend: str
    score: float
    close: float
    ema_20: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    rsi: float | None = None
    macd_histogram: float | None = None
    atr_percent: float | None = None
    adx: float | None = None
    volume_ratio: float | None = None
    vwap: float | None = None
    momentum: float | None = None
    trend_strength: float | None = None
    volatility: float | None = None
    structure: StructureSnapshot = field(default_factory=StructureSnapshot)


@dataclass(frozen=True)
class BitcoinAnalysis:
    available: bool
    bias: MarketBias
    trend: str
    score: float
    price: float | None
    timeframes: dict[str, TimeframeBiasSnapshot] = field(default_factory=dict)
    detail: str = ""

    def ema_status(self, preferred: str = "4h") -> str:
        snap = self.timeframes.get(preferred) or next(iter(self.timeframes.values()), None)
        if snap is None or snap.ema_20 is None or snap.ema_50 is None or snap.close <= 0:
            return "unknown"
        parts: list[str] = []
        parts.append("above_ema20" if snap.close > snap.ema_20 else "below_ema20")
        parts.append("above_ema50" if snap.close > snap.ema_50 else "below_ema50")
        if snap.ema_200 is not None:
            parts.append("above_ema200" if snap.close > snap.ema_200 else "below_ema200")
        if snap.ema_20 > snap.ema_50:
            parts.append("ema_stack_bull")
        elif snap.ema_20 < snap.ema_50:
            parts.append("ema_stack_bear")
        return ",".join(parts)


@dataclass(frozen=True)
class EthereumAnalysis:
    available: bool
    bias: MarketBias = MarketBias.NEUTRAL
    trend: str = "neutral"
    score: float = 0.0
    relative_strength_vs_btc: float | None = None
    detail: str = "stub"


@dataclass(frozen=True)
class DominanceAnalysis:
    available: bool
    btc_dominance: float | None = None
    btc_dominance_trend: DominanceTrend | None = None
    usdt_dominance: float | None = None
    usdt_risk_mode: RiskMode | None = None
    total3_trend: str | None = None
    total3_breadth: str | None = None
    score: float = 0.0
    detail: str = "stub"


@dataclass(frozen=True)
class FearGreedAnalysis:
    available: bool
    value: int | None = None
    band: FearGreedBand | None = None
    score: float = 0.0
    detail: str = "stub"


@dataclass(frozen=True)
class FundingAnalysis:
    available: bool
    symbol_rate: float | None = None
    btc_rate: float | None = None
    symbol_avg: float | None = None
    btc_avg: float | None = None
    symbol_change: float | None = None
    status: FundingStatus = FundingStatus.NEUTRAL
    score: float = 0.0
    detail: str = "stub"


@dataclass(frozen=True)
class OpenInterestAnalysis:
    available: bool
    symbol_oi: float | None = None
    btc_oi: float | None = None
    symbol_oi_change_pct: float | None = None
    relation: OiPriceRelation = OiPriceRelation.UNKNOWN
    score: float = 0.0
    detail: str = "stub"


@dataclass(frozen=True)
class LiquidationAnalysis:
    available: bool
    long_liquidations_usd: float | None = None
    short_liquidations_usd: float | None = None
    score: float = 0.0
    #: Free-venue Liquidity Score (same scale as ``score`` when source=free_venues).
    liquidity_score: float | None = None
    venues: tuple[str, ...] = ()
    long_share: float | None = None
    book_imbalance: float | None = None
    avg_funding: float | None = None
    source: str = "stub"
    extras: dict[str, Any] | None = None
    detail: str = "stub"


@dataclass
class ScoreWeights:
    coin: float = 0.60
    global_market: float = 0.25
    funding: float = 0.05
    open_interest: float = 0.05
    liquidations: float = 0.05

    def normalized(
        self,
        *,
        has_global: bool,
        has_funding: bool,
        has_oi: bool,
        has_liquidations: bool,
    ) -> dict[str, float]:
        raw = {
            "coin": self.coin,
            "global": self.global_market if has_global else 0.0,
            "funding": self.funding if has_funding else 0.0,
            "open_interest": self.open_interest if has_oi else 0.0,
            "liquidations": self.liquidations if has_liquidations else 0.0,
        }
        total = sum(raw.values())
        if total <= 0:
            return {"coin": 1.0, "global": 0.0, "funding": 0.0, "open_interest": 0.0, "liquidations": 0.0}
        return {key: value / total for key, value in raw.items()}


@dataclass
class BlendedScoreResult:
    final_score: float
    coin_score: float
    global_score: float
    funding_score: float
    oi_score: float
    liquidation_score: float
    weights_used: dict[str, float]
    detail: str


@dataclass
class MarketRegimeSnapshot:
    """Full market context at one point in time."""

    available: bool
    bias: MarketBias
    btc: BitcoinAnalysis
    eth: EthereumAnalysis
    dominance: DominanceAnalysis
    fear_greed: FearGreedAnalysis
    funding: FundingAnalysis
    open_interest: OpenInterestAnalysis
    liquidations: LiquidationAnalysis
    global_score: float
    captured_at: datetime
    detail: str = ""

    def to_context_dict(self) -> dict[str, Any]:
        """Compact dict persisted on trades / exposed on the desk."""
        btc_tf = self.btc.timeframes.get("4h") or next(iter(self.btc.timeframes.values()), None)
        return {
            "bias": self.bias.value,
            "biasLabel": self.bias.label,
            "globalScore": round(self.global_score, 2),
            "available": self.available,
            "capturedAt": self.captured_at.isoformat().replace("+00:00", "Z"),
            "btc": {
                "price": self.btc.price,
                "bias": self.btc.bias.value,
                "trend": self.btc.trend,
                "rsi": btc_tf.rsi if btc_tf else None,
                "emaStatus": self.btc.ema_status(),
                "volatility": btc_tf.volatility if btc_tf else None,
                "atrPercent": btc_tf.atr_percent if btc_tf else None,
            },
            "eth": {
                "available": self.eth.available,
                "bias": self.eth.bias.value if self.eth.available else None,
                "relativeStrengthVsBtc": self.eth.relative_strength_vs_btc,
            },
            "dominance": {
                "btcD": self.dominance.btc_dominance,
                "btcDTrend": (
                    self.dominance.btc_dominance_trend.value
                    if self.dominance.btc_dominance_trend
                    else None
                ),
                "usdtD": self.dominance.usdt_dominance,
                "usdtRiskMode": (
                    self.dominance.usdt_risk_mode.value if self.dominance.usdt_risk_mode else None
                ),
                "total3Trend": self.dominance.total3_trend,
            },
            "fearGreed": {
                "value": self.fear_greed.value,
                "band": self.fear_greed.band.value if self.fear_greed.band else None,
            },
            "funding": {
                "status": self.funding.status.value if self.funding.available else None,
                "symbolRate": self.funding.symbol_rate,
                "btcRate": self.funding.btc_rate,
            },
            "openInterest": {
                "available": self.open_interest.available,
                "relation": self.open_interest.relation.value,
                "symbolOi": self.open_interest.symbol_oi,
                "changePct": self.open_interest.symbol_oi_change_pct,
            },
            "liquidations": {
                "available": self.liquidations.available,
                "longUsd": self.liquidations.long_liquidations_usd,
                "shortUsd": self.liquidations.short_liquidations_usd,
                "liquidityScore": self.liquidations.liquidity_score,
                "venues": list(self.liquidations.venues),
                "longShare": self.liquidations.long_share,
                "bookImbalance": self.liquidations.book_imbalance,
                "avgFunding": self.liquidations.avg_funding,
                "source": self.liquidations.source,
            },
            "detail": self.detail,
        }

    def to_desk_dict(self) -> dict[str, Any]:
        return {
            "bias": self.bias.value,
            "biasLabel": self.bias.label,
            "btcTrend": self.btc.trend,
            "btcBias": self.btc.bias.value,
            "btcD": self.dominance.btc_dominance,
            "btcDTrend": (
                self.dominance.btc_dominance_trend.value
                if self.dominance.btc_dominance_trend
                else None
            ),
            "usdtD": self.dominance.usdt_dominance,
            "usdtRiskMode": (
                self.dominance.usdt_risk_mode.value if self.dominance.usdt_risk_mode else None
            ),
            "fundingStatus": self.funding.status.value if self.funding.available else None,
            "fearGreed": self.fear_greed.value,
            "fearGreedBand": self.fear_greed.band.value if self.fear_greed.band else None,
            "liquidityScore": self.liquidations.liquidity_score,
            "liquidityVenues": list(self.liquidations.venues) if self.liquidations.available else [],
            "globalScore": round(self.global_score, 2),
            "available": self.available,
            "capturedAt": self.captured_at.isoformat().replace("+00:00", "Z"),
        }


def bias_from_score(score: float, *, strong_threshold: float = 65.0) -> MarketBias:
    if score >= strong_threshold:
        return MarketBias.STRONG_BULLISH
    if score >= 25.0:
        return MarketBias.BULLISH
    if score <= -strong_threshold:
        return MarketBias.STRONG_BEARISH
    if score <= -25.0:
        return MarketBias.BEARISH
    return MarketBias.NEUTRAL


def empty_snapshot(captured_at: datetime, detail: str = "unavailable") -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        available=False,
        bias=MarketBias.NEUTRAL,
        btc=BitcoinAnalysis(False, MarketBias.NEUTRAL, "neutral", 0.0, None, detail=detail),
        eth=EthereumAnalysis(False, detail=detail),
        dominance=DominanceAnalysis(False, detail=detail),
        fear_greed=FearGreedAnalysis(False, detail=detail),
        funding=FundingAnalysis(False, detail=detail),
        open_interest=OpenInterestAnalysis(False, detail=detail),
        liquidations=LiquidationAnalysis(False, detail=detail),
        global_score=0.0,
        captured_at=captured_at,
        detail=detail,
    )


def dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {k: dataclass_to_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {k: dataclass_to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_dict(v) for v in value]
    return value
