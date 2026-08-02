"""Institutional Knowledge Base — gates, phase, narrative, orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import Confidence, MarketPhase, SignalDirection
from app.core.config import Settings
from app.intelligence.orchestrator import InstitutionalIntelligenceOrchestrator
from app.intelligence.phase import classify_market_phase
from app.intelligence.narrative import classify_narrative
from app.intelligence.types import InstitutionalPhase, MarketNarrative
from app.knowledge.hierarchy import DECISION_HIERARCHY, DecisionStep
from app.knowledge.no_trade import NoTradeContext, NoTradeGate, evaluate_no_trade_gates
from app.market_regime.types import (
    BitcoinAnalysis,
    DominanceAnalysis,
    EthereumAnalysis,
    FearGreedAnalysis,
    FundingAnalysis,
    LiquidationAnalysis,
    MarketBias,
    MarketRegimeSnapshot,
    OpenInterestAnalysis,
)
from app.signals.types import RiskParameters, SignalResult


def _empty_regime(bias: MarketBias = MarketBias.NEUTRAL) -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        available=True,
        bias=bias,
        btc=BitcoinAnalysis(
            available=True,
            bias=bias,
            trend="neutral",
            score=bias.score,
            price=100_000.0,
        ),
        eth=EthereumAnalysis(available=False),
        dominance=DominanceAnalysis(available=False),
        fear_greed=FearGreedAnalysis(available=False),
        funding=FundingAnalysis(available=False),
        open_interest=OpenInterestAnalysis(available=False),
        liquidations=LiquidationAnalysis(available=False),
        global_score=50.0,
        captured_at=datetime.now(UTC),
        detail="test",
    )


def _signal(
    *,
    direction: SignalDirection = SignalDirection.LONG,
    score: float = 80.0,
    confidence: Confidence = Confidence.MEDIUM,
) -> SignalResult:
    return SignalResult(
        symbol="TESTUSDT",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        direction=direction,
        score=score,
        confidence=confidence,
        market_phase=MarketPhase.UPTREND,
        primary_timeframe="4h",
        analyzed_timeframes=["4h"],
        reference_price=1.0,
        data_quality=90.0,
        components=[],
        assessments={},
        risk=RiskParameters(
            entry_low=1.0,
            entry_high=1.01,
            stop_loss=0.95,
            take_profit_1=1.1,
            take_profit_2=1.15,
            take_profit_3=1.2,
            risk_reward_ratio=2.0,
            risk_percent=1.0,
            suggested_position_size=100.0,
            stop_distance_percent=5.0,
            invalidation_note="test",
        ),
        reasons=["unit test setup"],
    )


def test_decision_hierarchy_order() -> None:
    assert DECISION_HIERARCHY[0] is DecisionStep.MARKET_PHASE
    assert DECISION_HIERARCHY[-1] is DecisionStep.TRADE_DECISION
    assert len(DECISION_HIERARCHY) == 12


def test_no_trade_strong_bull_blocks_short() -> None:
    verdict = evaluate_no_trade_gates(
        NoTradeContext(
            direction=SignalDirection.SHORT,
            trade_score=20.0,
            confidence_pct=70.0,
            data_quality=90.0,
            min_trade_score=75.0,
            min_confidence_pct=55.0,
            min_data_quality=70.0,
            min_risk_reward=1.5,
            market_regime=_empty_regime(MarketBias.STRONG_BULLISH),
            regime_hard_veto=True,
        )
    )
    assert verdict.reject
    assert NoTradeGate.REGIME_AGAINST in verdict.gates


def test_no_trade_low_confidence() -> None:
    verdict = evaluate_no_trade_gates(
        NoTradeContext(
            direction=SignalDirection.LONG,
            trade_score=90.0,
            confidence_pct=40.0,
            data_quality=90.0,
            min_trade_score=75.0,
            min_confidence_pct=55.0,
            min_data_quality=70.0,
            min_risk_reward=1.5,
        )
    )
    assert verdict.reject
    assert NoTradeGate.CONFIDENCE_BELOW in verdict.gates


def test_phase_strong_bull() -> None:
    phase = classify_market_phase(_empty_regime(MarketBias.STRONG_BULLISH))
    assert phase.phase is InstitutionalPhase.TRENDING_BULLISH


def test_narrative_neutral_defaults() -> None:
    snap = classify_narrative(_empty_regime(MarketBias.NEUTRAL))
    assert snap.primary in (
        MarketNarrative.RANGE_COMPRESSION,
        MarketNarrative.TREND_CONTINUATION,
        MarketNarrative.UNCERTAIN,
    )
    assert snap.confidence > 0


def test_orchestrator_market_intel_before_finalize() -> None:
    settings = Settings(
        app_env="test",
        institutional_kb_enabled=True,
        institutional_enforce_gates=False,
    )
    orch = InstitutionalIntelligenceOrchestrator(settings)
    ctx = orch.build_market_intelligence(_empty_regime(MarketBias.BULLISH))
    assert ctx.completed
    assert ctx.phase is not None
    assert ctx.narrative is not None
    assert ctx.structure is not None
    assert ctx.data_quality is not None
    assert "market_phase" in ctx.pipeline_steps

    result = _signal()
    explain = orch.finalize_trade(result, ctx)
    assert explain.confidence_pct > 0
    assert explain.probability is not None
    assert explain.gap is not None
    assert explain.natural_language
    # test env: gates advisory unless explicitly enforced
    assert result.direction is SignalDirection.LONG


def test_orchestrator_enforce_gates_can_block() -> None:
    settings = Settings(
        app_env="test",
        institutional_kb_enabled=True,
        institutional_enforce_gates=True,
        institutional_min_confidence_pct=95.0,
    )
    orch = InstitutionalIntelligenceOrchestrator(settings)
    ctx = orch.build_market_intelligence(_empty_regime(MarketBias.NEUTRAL))
    result = _signal(confidence=Confidence.LOW, score=80.0)
    explain = orch.finalize_trade(result, ctx)
    assert result.direction is SignalDirection.NO_TRADE
    assert explain.no_trade_gates
    assert "confidence_below_threshold" in explain.no_trade_gates
