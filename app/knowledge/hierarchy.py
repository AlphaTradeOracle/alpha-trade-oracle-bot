"""Mandatory decision hierarchy (KB Part 1)."""

from __future__ import annotations

from enum import StrEnum


class DecisionStep(StrEnum):
    MARKET_PHASE = "market_phase"
    GLOBAL_MARKET_REGIME = "global_market_regime"
    BITCOIN_ANALYSIS = "bitcoin_analysis"
    MARKET_STRUCTURE = "market_structure"
    LIQUIDITY = "liquidity"
    TREND_ANALYSIS = "trend_analysis"
    SMART_MONEY = "smart_money"
    VOLUME_ANALYSIS = "volume_analysis"
    MOMENTUM = "momentum"
    CONFLUENCE_SCORE = "confluence_score"
    RISK_ASSESSMENT = "risk_assessment"
    TRADE_DECISION = "trade_decision"


DECISION_HIERARCHY: tuple[DecisionStep, ...] = (
    DecisionStep.MARKET_PHASE,
    DecisionStep.GLOBAL_MARKET_REGIME,
    DecisionStep.BITCOIN_ANALYSIS,
    DecisionStep.MARKET_STRUCTURE,
    DecisionStep.LIQUIDITY,
    DecisionStep.TREND_ANALYSIS,
    DecisionStep.SMART_MONEY,
    DecisionStep.VOLUME_ANALYSIS,
    DecisionStep.MOMENTUM,
    DecisionStep.CONFLUENCE_SCORE,
    DecisionStep.RISK_ASSESSMENT,
    DecisionStep.TRADE_DECISION,
)

#: Market Intelligence sub-order (KB Part 2).
MARKET_INTEL_ORDER: tuple[str, ...] = (
    "bitcoin",
    "ethereum",
    "btc_dominance",
    "usdt_dominance",
    "total3",
    "funding",
    "open_interest",
    "liquidations",
    "fear_greed",
    "macro_events",
    "global_market_bias",
)
