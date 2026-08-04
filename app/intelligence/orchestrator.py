"""Institutional Intelligence Orchestrator (KB Parts 1–9 pipeline)."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.intelligence.adaptive import evaluate_adaptive_performance
from app.intelligence.data_quality import evaluate_data_quality
from app.intelligence.decision import (
    build_natural_language,
    compute_confidence_pct,
    decide_label,
)
from app.intelligence.gap import analyze_decision_gap
from app.intelligence.historical import match_historical_patterns
from app.intelligence.narrative import classify_narrative
from app.intelligence.phase import classify_market_phase
from app.intelligence.probability import estimate_probabilities
from app.intelligence.structure_context import build_structure_context
from app.intelligence.types import InstitutionalContext, TradeExplainability
from app.knowledge.hierarchy import DECISION_HIERARCHY, MARKET_INTEL_ORDER
from app.knowledge.no_trade import NoTradeContext, evaluate_no_trade_gates
from app.knowledge.principles import GLOBAL_OBJECTIVE
from app.market_regime.types import MarketRegimeSnapshot
from app.signals.types import SignalResult

logger = get_logger(__name__)


class InstitutionalIntelligenceOrchestrator:
    """
    Enforces mandatory analysis order:

    1. Data quality + Market Intelligence (before coin analysis)
    2. Per-coin signal (caller)
    3. Probability / gap / adaptive finalization + no-trade gates
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._settings, "institutional_kb_enabled", True))

    @property
    def enforce_gates(self) -> bool:
        """When true, no-trade gates may force SignalDirection.NO_TRADE."""
        return bool(getattr(self._settings, "institutional_enforce_gates", False))

    def build_market_intelligence(
        self,
        regime: MarketRegimeSnapshot | None,
        *,
        candle_data_quality: float = 100.0,
        exchange_ok: bool = True,
    ) -> InstitutionalContext:
        """Run Parts 2–4 / 7–8 market-level engines BEFORE coin analysis."""
        steps = list(MARKET_INTEL_ORDER)
        ctx = InstitutionalContext(pipeline_steps=steps)

        if not self.enabled:
            ctx.completed = True
            ctx.natural_language = "Institutional KB disabled."
            return ctx

        min_dq = float(getattr(self._settings, "institutional_min_data_quality", 70.0))
        ctx.data_quality = evaluate_data_quality(
            candle_data_quality=candle_data_quality,
            regime=regime,
            exchange_ok=exchange_ok,
            min_quality=min_dq,
        )
        ctx.pipeline_steps.append("data_quality")
        ctx.warnings.extend(ctx.data_quality.warnings)

        ctx.market_regime = regime
        ctx.phase = classify_market_phase(regime)
        ctx.pipeline_steps.append("market_phase")

        ctx.narrative = classify_narrative(regime)
        ctx.pipeline_steps.append("market_narrative")

        ctx.structure = build_structure_context(regime)
        ctx.pipeline_steps.append("market_structure")

        ctx.historical = match_historical_patterns(ctx)
        ctx.pipeline_steps.append("historical_patterns")

        ctx.adaptive = evaluate_adaptive_performance(ctx)
        ctx.pipeline_steps.append("adaptive_intelligence")

        ctx.completed = True
        ctx.natural_language = self._market_summary(ctx)
        logger.info(
            "market_intelligence_completed",
            phase=ctx.phase.phase.value if ctx.phase else None,
            bias=ctx.bias.value if ctx.bias else None,
            narrative=ctx.narrative.primary.value if ctx.narrative else None,
            data_quality=ctx.data_quality.quality_score if ctx.data_quality else None,
            objective=GLOBAL_OBJECTIVE[:80],
        )
        return ctx

    def finalize_trade(
        self,
        result: SignalResult,
        ctx: InstitutionalContext,
    ) -> TradeExplainability:
        """Parts 5/6/9 — probability, gap, gates, explainability after coin score."""
        if not self.enabled:
            return TradeExplainability(
                trade_score=result.score,
                confidence_pct=50.0,
                decision=decide_label(result, confidence_pct=50.0, rejected=False),
                reasons=list(result.reasons[:5]),
                natural_language="Institutional KB disabled.",
            )

        probability = estimate_probabilities(result, ctx)
        confidence_pct = compute_confidence_pct(result, ctx)

        min_trade = float(self._settings.signal_min_score)
        min_conf = float(getattr(self._settings, "institutional_min_confidence_pct", 55.0))
        min_dq = float(getattr(self._settings, "institutional_min_data_quality", 70.0))
        min_rr = float(self._settings.min_risk_reward_ratio)
        require_ev = bool(getattr(self._settings, "institutional_require_positive_ev", False))

        gap = analyze_decision_gap(
            result,
            ctx,
            min_trade_score=min_trade,
            min_confidence_pct=min_conf,
        )

        liq_ok = True
        if ctx.structure is not None and ctx.structure.liquidity_status == "thin":
            # Soft warning by default; hard only when configured.
            if getattr(self._settings, "institutional_reject_thin_liquidity", False):
                liq_ok = False

        no_trade = evaluate_no_trade_gates(
            NoTradeContext(
                direction=result.direction,
                trade_score=result.score,
                confidence_pct=confidence_pct,
                data_quality=(
                    ctx.data_quality.quality_score if ctx.data_quality else result.data_quality
                ),
                min_trade_score=min_trade,
                min_confidence_pct=min_conf,
                min_data_quality=min_dq,
                min_risk_reward=min_rr,
                risk_reward=result.risk.risk_reward_ratio if result.risk else None,
                expected_value=probability.expected_value if require_ev else None,
                market_regime=ctx.market_regime,
                regime_hard_veto=bool(self._settings.market_regime_hard_veto),
                regime_fail_closed=bool(
                    getattr(self._settings, "market_regime_fail_closed", True)
                ),
                exchange_data_ok=True,
                critical_data_ok=not (
                    ctx.data_quality.trade_restricted if ctx.data_quality else False
                ),
                liquidity_ok=liq_ok,
                extra_warnings=list(ctx.warnings),
            )
        )

        rejected = no_trade.reject
        if rejected and result.direction.is_actionable and self.enforce_gates:
            from app.core.enums import SignalDirection

            result.direction = SignalDirection.NO_TRADE
            result.no_trade_reason = no_trade.primary_reason or "institutional_no_trade_gate"
            result.reasons.append(f"Institutional gate: {result.no_trade_reason}")
        elif rejected and result.direction.is_actionable:
            result.counter_arguments.append(
                f"Institutional gate (advisory): {no_trade.primary_reason}"
            )

        decision = decide_label(
            result,
            confidence_pct=confidence_pct,
            rejected=rejected and self.enforce_gates,
        )

        engine_scores = {
            "trade": result.score,
            "coin": result.coin_score if result.coin_score is not None else result.score,
            "data_quality": ctx.data_quality.quality_score if ctx.data_quality else result.data_quality,
            "structure": ctx.structure.structure_score if ctx.structure else 0.0,
            "narrative": ctx.narrative.confidence if ctx.narrative else 0.0,
            "phase": ctx.phase.confidence if ctx.phase else 0.0,
            "adaptive": ctx.adaptive.performance_score if ctx.adaptive else 0.0,
            "global_regime": (
                ctx.market_regime.global_score
                if ctx.market_regime and ctx.market_regime.available
                else 0.0
            ),
        }

        explain = TradeExplainability(
            trade_score=result.score,
            confidence_pct=confidence_pct,
            decision=decision,
            expected_value=probability.expected_value,
            win_probability=probability.win_probability,
            loss_probability=probability.loss_probability,
            reasons=list(result.reasons[:12]),
            warnings=list(no_trade.warnings) + list(gap.negative_factors)[:5],
            missing_factors=list(gap.missing_confirmations),
            rejected_factors=list(no_trade.reasons),
            engine_scores=engine_scores,
            gap=gap,
            probability=probability,
            no_trade_gates=[g.value for g in no_trade.gates],
            natural_language=build_natural_language(
                result,
                ctx,
                decision=decision,
                confidence_pct=confidence_pct,
                expected_value=probability.expected_value,
            ),
        )

        # Attach hierarchy audit trail on market_context later by caller.
        logger.info(
            "institutional_trade_finalized",
            symbol=result.symbol,
            decision=decision.value,
            trade_score=result.score,
            confidence_pct=confidence_pct,
            expected_value=probability.expected_value,
            rejected=rejected,
            gates=explain.no_trade_gates,
            hierarchy=[s.value for s in DECISION_HIERARCHY],
        )
        return explain

    @staticmethod
    def _market_summary(ctx: InstitutionalContext) -> str:
        bits: list[str] = ["Market Intelligence complete."]
        if ctx.phase:
            bits.append(f"Phase={ctx.phase.phase.label} ({ctx.phase.confidence:.0f}% conf).")
        if ctx.bias:
            bits.append(f"Bias={ctx.bias.label}.")
        if ctx.narrative:
            bits.append(f"Narrative={ctx.narrative.primary.label}.")
        if ctx.structure:
            bits.append(f"Structure={ctx.structure.structure_label}.")
        if ctx.data_quality:
            bits.append(f"DataQuality={ctx.data_quality.quality_score:.0f}.")
        return " ".join(bits)
