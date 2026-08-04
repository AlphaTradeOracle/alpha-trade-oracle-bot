"""Mandatory NO-TRADE gates (KB Part 1 / Part 5 / Part 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.enums import SignalDirection
from app.market_regime.types import MarketBias, MarketRegimeSnapshot


class NoTradeGate(StrEnum):
    REGIME_AGAINST = "regime_strongly_against"
    REGIME_UNAVAILABLE = "regime_unavailable"
    TRADE_SCORE_BELOW = "trade_score_below_threshold"
    CONFIDENCE_BELOW = "confidence_below_threshold"
    DATA_QUALITY_BELOW = "data_quality_below_threshold"
    MISSING_CRITICAL_DATA = "critical_market_data_missing"
    EXCHANGE_DATA_UNAVAILABLE = "exchange_data_unavailable"
    RISK_REWARD_BELOW = "risk_reward_below_minimum"
    EXPECTED_VALUE_NON_POSITIVE = "expected_value_non_positive"
    MACRO_EVENT = "high_impact_macro_event"
    PORTFOLIO_RISK = "maximum_portfolio_risk_exceeded"
    DAILY_DRAWDOWN = "maximum_daily_drawdown_exceeded"
    WEEKLY_DRAWDOWN = "maximum_weekly_drawdown_exceeded"
    LIQUIDITY_INSUFFICIENT = "liquidity_insufficient"


@dataclass(frozen=True)
class NoTradeVerdict:
    reject: bool
    gates: tuple[NoTradeGate, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def primary_reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


@dataclass
class NoTradeContext:
    direction: SignalDirection
    trade_score: float
    confidence_pct: float
    data_quality: float
    min_trade_score: float
    min_confidence_pct: float
    min_data_quality: float
    min_risk_reward: float
    risk_reward: float | None = None
    expected_value: float | None = None
    market_regime: MarketRegimeSnapshot | None = None
    regime_hard_veto: bool = True
    regime_fail_closed: bool = True
    exchange_data_ok: bool = True
    critical_data_ok: bool = True
    macro_high_impact: bool = False
    portfolio_risk_ok: bool = True
    daily_drawdown_ok: bool = True
    weekly_drawdown_ok: bool = True
    liquidity_ok: bool = True
    extra_warnings: list[str] = field(default_factory=list)


def evaluate_no_trade_gates(ctx: NoTradeContext) -> NoTradeVerdict:
    """Evaluate mandatory rejection gates. First hard failure wins for logging."""
    gates: list[NoTradeGate] = []
    reasons: list[str] = []
    warnings = list(ctx.extra_warnings)

    if not ctx.exchange_data_ok:
        gates.append(NoTradeGate.EXCHANGE_DATA_UNAVAILABLE)
        reasons.append("Exchange data unavailable")
    if not ctx.critical_data_ok:
        gates.append(NoTradeGate.MISSING_CRITICAL_DATA)
        reasons.append("Critical market data missing")
    if ctx.data_quality < ctx.min_data_quality:
        gates.append(NoTradeGate.DATA_QUALITY_BELOW)
        reasons.append(
            f"Data quality {ctx.data_quality:.1f} < minimum {ctx.min_data_quality:.1f}"
        )
    if ctx.macro_high_impact:
        gates.append(NoTradeGate.MACRO_EVENT)
        reasons.append("High-impact macro event — new trades rejected")
    if not ctx.portfolio_risk_ok:
        gates.append(NoTradeGate.PORTFOLIO_RISK)
        reasons.append("Maximum portfolio risk exceeded")
    if not ctx.daily_drawdown_ok:
        gates.append(NoTradeGate.DAILY_DRAWDOWN)
        reasons.append("Maximum daily drawdown exceeded")
    if not ctx.weekly_drawdown_ok:
        gates.append(NoTradeGate.WEEKLY_DRAWDOWN)
        reasons.append("Maximum weekly drawdown exceeded")
    if not ctx.liquidity_ok:
        gates.append(NoTradeGate.LIQUIDITY_INSUFFICIENT)
        reasons.append("Liquidity insufficient for institutional entry")

    if ctx.regime_hard_veto:
        if (
            ctx.regime_fail_closed
            and (ctx.market_regime is None or not ctx.market_regime.available)
            and ctx.direction.is_actionable
        ):
            gates.append(NoTradeGate.REGIME_UNAVAILABLE)
            reasons.append("Market regime unavailable — entries blocked (fail-closed)")
        elif ctx.market_regime is not None and ctx.market_regime.available:
            bias = ctx.market_regime.bias
            if bias is MarketBias.STRONG_BULLISH and ctx.direction.is_short:
                gates.append(NoTradeGate.REGIME_AGAINST)
                reasons.append(f"Global regime {bias.label} strongly against short setup")
            elif bias is MarketBias.STRONG_BEARISH and ctx.direction.is_long:
                gates.append(NoTradeGate.REGIME_AGAINST)
                reasons.append(f"Global regime {bias.label} strongly against long setup")

    if ctx.direction.is_actionable and ctx.trade_score < ctx.min_trade_score:
        # For shorts the engine uses low scores — skip this gate for shorts here;
        # short thresholds are enforced separately by SignalEngine / paper.
        if ctx.direction.is_long:
            gates.append(NoTradeGate.TRADE_SCORE_BELOW)
            reasons.append(
                f"Trade score {ctx.trade_score:.1f} < minimum {ctx.min_trade_score:.1f}"
            )

    if ctx.confidence_pct < ctx.min_confidence_pct:
        gates.append(NoTradeGate.CONFIDENCE_BELOW)
        reasons.append(
            f"Confidence {ctx.confidence_pct:.1f}% < minimum {ctx.min_confidence_pct:.1f}%"
        )

    if ctx.risk_reward is not None and ctx.risk_reward < ctx.min_risk_reward:
        gates.append(NoTradeGate.RISK_REWARD_BELOW)
        reasons.append(
            f"Risk/Reward {ctx.risk_reward:.2f} < minimum {ctx.min_risk_reward:.2f}"
        )

    if ctx.expected_value is not None and ctx.expected_value <= 0:
        gates.append(NoTradeGate.EXPECTED_VALUE_NON_POSITIVE)
        reasons.append(f"Expected value {ctx.expected_value:.4f} is not positive")

    return NoTradeVerdict(
        reject=bool(gates),
        gates=tuple(gates),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )
