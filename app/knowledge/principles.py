"""Core principles from Institutional KB Part 1 — mandatory, not optional."""

from __future__ import annotations

GLOBAL_OBJECTIVE = (
    "Identify situations where probability, risk, liquidity and market context "
    "create a statistical edge. Maximize Expected Value (EV), never forecast."
)

CORE_PRINCIPLES: tuple[str, ...] = (
    "Never rely on a single indicator — require multiple independent confirmations.",
    "Indicators never generate trades by themselves; they only provide evidence.",
    "Market structure has higher priority than oscillators.",
    "Price action has higher priority than lagging indicators.",
    "Liquidity has higher priority than indicators.",
    "Confluence has higher priority than individual signals.",
    "Avoid unnecessary complexity — only statistically useful information.",
    "Every score must be explainable, reproducible, and logged.",
    "Coin analysis must not begin before Market Intelligence completes.",
    "No trade may ignore Global Market Regime when it strongly opposes the setup.",
)

#: Structure > SMC > Volume > Trend > Momentum > Indicators
STRUCTURE_PRIORITY: tuple[str, ...] = (
    "liquidity",
    "market_structure",
    "smart_money",
    "volume",
    "trend",
    "momentum",
    "indicators",
)
