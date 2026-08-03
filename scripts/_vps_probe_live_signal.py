#!/usr/bin/env python3
"""Probe: one live analysis under current strategy + recent signal market_context."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select, text

from app.container import build_container
from app.core.config import get_settings
from app.database.session import session_scope
from app.models.signal import Signal
from app.models.market import Asset


async def main() -> None:
    s = get_settings()
    print("=== LIVE FLAGS ===")
    for k in (
        "market_regime_enabled",
        "market_regime_hard_veto",
        "regime_filter_enabled",
        "institutional_kb_enabled",
        "institutional_enforce_gates",
        "signal_short_max_score",
        "signal_min_score",
        "signal_require_strong",
        "telegram_signal_dispatch",
    ):
        print(f"{k}={getattr(s, k)}")

    container = build_container(s)
    symbol = "BTCUSDT"
    async with session_scope() as session:
        outcome = await container.analysis_service.analyze(
            symbol, session=session, persist=False, use_llm=False
        )
        r = outcome.result
        blend = (r.market_context or {}).get("blend") or {}
        intel = (r.market_context or {}).get("intelligence") or {}
        expl = (r.market_context or {}).get("explainability") or {}
        print("=== LIVE ANALYZE", symbol, "===")
        print(
            "direction",
            r.direction.value,
            "score",
            round(r.score, 2),
            "coin",
            None if r.coin_score is None else round(r.coin_score, 2),
            "actionable",
            r.is_actionable,
        )
        print("blend_final", blend.get("finalScore"), "coin", blend.get("coinScore"))
        print(
            "bias",
            (intel.get("marketRegime") or intel.get("bias") or intel.get("phase")),
        )
        print("decision", expl.get("decision"), "conf", expl.get("confidence_pct"))
        print("has_blend", bool(blend), "has_intel", bool(intel), "has_expl", bool(expl))

        rows = (
            await session.execute(
                text(
                    """
                    SELECT a.symbol, s.direction, ROUND(s.score::numeric,2) AS score,
                           s.created_at,
                           (s.market_context ? 'blend') AS has_blend,
                           (s.market_context ? 'intelligence') AS has_intel,
                           (s.market_context ? 'explainability') AS has_expl,
                           s.market_context->'blend'->>'finalScore' AS final_score,
                           s.market_context->'blend'->>'coinScore' AS coin_score,
                           s.market_context->'explainability'->>'decision' AS decision
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at >= NOW() - INTERVAL '24 hours'
                    ORDER BY s.created_at DESC
                    LIMIT 12
                    """
                )
            )
        ).mappings().all()
        print("=== LAST 24h SIGNALS ===")
        for row in rows:
            print(dict(row))

    await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
