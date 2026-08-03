"""Trace why score-passing shorts still get suppressed."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.container import build_container
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.signals.dedup import SignalDeduplicator
from app.repositories.signal_repository import SignalRepository
from app.core.enums import SignalDirection
from app.signals.types import RiskParameters, SignalResult
from app.core.enums import Confidence, MarketPhase
from app.core.time import ensure_utc, utc_now


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    c = build_container()
    s = c.settings
    print("require_strong", s.signal_require_strong)
    print("regime_filter_enabled", s.regime_filter_enabled)
    print("market_regime_hard_veto", s.market_regime_hard_veto)
    print("min_rr", s.min_risk_reward_ratio)
    print("short_max", s.signal_short_max_score, "short_min", s.signal_short_min_score)
    print("cooldown", s.signal_cooldown_minutes)

    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT s.id, a.symbol, s.direction, s.score, s.data_quality,
                           s.risk_reward_ratio, s.no_trade_reason, s.expires_at, s.created_at,
                           s.primary_timeframe
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at > NOW() - INTERVAL '24 hours'
                      AND s.direction IN ('SHORT','STRONG_SHORT')
                      AND s.score <= 25 AND s.score > 18
                    ORDER BY s.created_at DESC
                    LIMIT 10
                    """
                )
            )
        ).mappings().all()

        dedup = SignalDeduplicator(
            cooldown_minutes=s.signal_cooldown_minutes,
            redis_client=c.redis if hasattr(c, "redis") else None,
        )

        print("\n=== RE-EVALUATE SAMPLE SHORT_PASS ===")
        for r in rows:
            print("---", dict(r))
            dels = (
                await session.execute(
                    text(
                        """
                        SELECT status, suppression_reason, created_at, telegram_chat_id
                        FROM signal_deliveries WHERE signal_id=:sid ORDER BY id
                        """
                    ),
                    {"sid": r["id"]},
                )
            ).all()
            print("  deliveries", dels)

            # rebuild minimal SignalResult for dedup
            direction = SignalDirection(r["direction"])
            risk = RiskParameters(
                entry_low=1.0,
                entry_high=1.0,
                stop_loss=1.1,
                take_profit_1=0.9,
                take_profit_2=0.8,
                take_profit_3=0.7,
                risk_reward_ratio=float(r["risk_reward_ratio"] or 0),
                risk_percent=1.0,
                suggested_position_size=0.0,
                stop_distance_percent=1.0,
                invalidation_note="",
            )
            result = SignalResult(
                symbol=r["symbol"],
                created_at=ensure_utc(r["created_at"]),
                expires_at=ensure_utc(r["expires_at"]),
                direction=direction,
                score=float(r["score"]),
                confidence=Confidence.MEDIUM,
                market_phase=MarketPhase.UNKNOWN if hasattr(MarketPhase, "UNKNOWN") else list(MarketPhase)[0],
                primary_timeframe=r["primary_timeframe"] or "1h",
                analyzed_timeframes=["1h"],
                reference_price=1.0,
                data_quality=float(r["data_quality"] or 0),
                components=[],
                assessments={},
                risk=risk,
                no_trade_reason=r["no_trade_reason"],
            )
            decision = await dedup.evaluate(
                result,
                min_score=s.signal_min_score,
                short_max_score=s.signal_short_max_score,
                short_min_score=s.signal_short_min_score,
                min_risk_reward_ratio=s.min_risk_reward_ratio,
                require_strong=s.signal_require_strong,
                market_regime=None,
                regime_filter_enabled=False,
                now=utc_now(),
            )
            print(
                "  reeval should_send=",
                decision.should_send,
                "reason=",
                decision.reason,
                "detail=",
                decision.detail,
            )

        print("\n=== SUPPRESSION REASONS FOR SHORT score 18-25 (24h) ===")
        reasons = (
            await session.execute(
                text(
                    """
                    SELECT d.suppression_reason, COUNT(*)
                    FROM signal_deliveries d
                    JOIN signals s ON s.id = d.signal_id
                    WHERE s.created_at > NOW() - INTERVAL '24 hours'
                      AND s.direction IN ('SHORT','STRONG_SHORT')
                      AND s.score <= 25 AND s.score > 18
                    GROUP BY 1 ORDER BY 2 DESC
                    """
                )
            )
        ).all()
        for x in reasons:
            print(x)

        print("\n=== WORKER DISPATCHER WIRED? ===")
        # Inspect how worker builds ScanService - read from running process env only
        print("telegram_signal_dispatch", s.telegram_signal_dispatch)

    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
