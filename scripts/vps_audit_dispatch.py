"""Audit whether Telegram/paper dispatch plumbing is healthy."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import text

from app.container import build_container
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    container = build_container()
    s = container.settings
    issues: list[str] = []

    print("=== CONFIG ===")
    checks = {
        "enable_scheduler": s.enable_scheduler,
        "enable_universe_scan": s.enable_universe_scan,
        "telegram_signal_dispatch": getattr(s, "telegram_signal_dispatch", None),
        "signal_min_score": s.signal_min_score,
        "signal_short_max_score": getattr(s, "signal_short_max_score", None),
        "signal_short_min_score": getattr(s, "signal_short_min_score", None),
        "telegram_bot_token_set": bool(getattr(s, "telegram_bot_token", None)),
        "allowed_chats": list(getattr(s, "telegram_allowed_chat_ids", []) or []),
        "admin_chats": list(getattr(s, "telegram_admin_chat_ids", []) or []),
        "enable_paper_trading": s.enable_paper_trading,
        "market_regime_hard_veto": getattr(s, "market_regime_hard_veto", None),
    }
    for k, v in checks.items():
        print(f"  {k}={v}")

    if not checks["telegram_signal_dispatch"]:
        issues.append("telegram_signal_dispatch=false")
    if not checks["telegram_bot_token_set"]:
        issues.append("telegram_bot_token missing")
    if not checks["allowed_chats"]:
        issues.append("no allowed telegram chats")

    # Dispatcher object present?
    dispatcher = getattr(container, "signal_dispatcher", None) or getattr(
        container.scan_service, "_dispatcher", None
    )
    print(f"  scan_service={type(container.scan_service).__name__}")
    print(f"  dispatcher={type(dispatcher).__name__ if dispatcher else None}")

    async with session_scope() as session:
        print("\n=== DELIVERY HISTORY ===")
        by_status = (
            await session.execute(
                text(
                    """
                    SELECT status, COUNT(*), MAX(created_at) AS last_at
                    FROM signal_deliveries
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                )
            )
        ).all()
        for row in by_status:
            print(f"  {row[0]}: n={row[1]} last={row[2]}")

        sent = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*), MAX(sent_at), MAX(created_at)
                    FROM signal_deliveries
                    WHERE status IN ('sent','delivered','ok')
                       OR (sent_at IS NOT NULL AND status NOT IN ('suppressed','failed'))
                    """
                )
            )
        ).one()
        print(f"  sentish_count={sent[0]} last_sent_at={sent[1]} last_created={sent[2]}")

        # Any non-suppressed in last 14d?
        recent_sent = (
            await session.execute(
                text(
                    """
                    SELECT d.id, d.status, d.sent_at, d.created_at, d.telegram_chat_id,
                           d.suppression_reason, a.symbol, s.direction,
                           ROUND(s.score::numeric,1) AS score
                    FROM signal_deliveries d
                    JOIN signals s ON s.id = d.signal_id
                    JOIN assets a ON a.id = s.asset_id
                    WHERE d.status <> 'suppressed'
                    ORDER BY d.created_at DESC
                    LIMIT 15
                    """
                )
            )
        ).all()
        print("\n=== LAST NON-SUPPRESSED DELIVERIES ===")
        if not recent_sent:
            print("  (none ever / none found)")
            issues.append("no_non_suppressed_deliveries_in_db")
        else:
            for r in recent_sent:
                print(tuple(r))

        print("\n=== LAST 48h SUPPRESSION BREAKDOWN ===")
        reasons = (
            await session.execute(
                text(
                    """
                    SELECT COALESCE(suppression_reason, status), COUNT(*)
                    FROM signal_deliveries
                    WHERE created_at > NOW() - INTERVAL '48 hours'
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                )
            )
        ).all()
        for r in reasons:
            print(f"  {r[0]}: {r[1]}")

        print("\n=== WOULD-DISPATCH CANDIDATES LAST SCAN WINDOW ===")
        # Actionable + score gates as configured
        min_long = float(s.signal_min_score)
        max_short = float(getattr(s, "signal_short_max_score", 25))
        cands = (
            await session.execute(
                text(
                    """
                    SELECT a.symbol, s.direction, ROUND(s.score::numeric,1) AS score, s.created_at
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at > NOW() - INTERVAL '2 hours'
                      AND (
                        (s.direction IN ('LONG','STRONG_LONG') AND s.score >= :min_long)
                        OR
                        (s.direction IN ('SHORT','STRONG_SHORT') AND s.score <= :max_short)
                      )
                    ORDER BY s.created_at DESC
                    LIMIT 30
                    """
                ),
                {"min_long": min_long, "max_short": max_short},
            )
        ).all()
        print(f"  gates: long>={min_long} short<={max_short}")
        print(f"  candidates_2h={len(cands)}")
        for r in cands:
            print(" ", tuple(r))
        if not cands:
            print("  (no gate-passing signals — dispatch idle by filters, not necessarily broken)")

        # Cross-check: actionable but failed gates
        near = (
            await session.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (
                        WHERE direction IN ('SHORT','STRONG_SHORT') AND score > :max_short AND score <= :max_short + 10
                      ) AS short_near_miss,
                      COUNT(*) FILTER (
                        WHERE direction IN ('LONG','STRONG_LONG') AND score < :min_long AND score >= :min_long - 10
                      ) AS long_near_miss,
                      COUNT(*) FILTER (
                        WHERE direction IN ('SHORT','STRONG_SHORT') AND score <= :max_short
                      ) AS short_pass,
                      COUNT(*) FILTER (
                        WHERE direction IN ('LONG','STRONG_LONG') AND score >= :min_long
                      ) AS long_pass
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                    """
                ),
                {"min_long": min_long, "max_short": max_short},
            )
        ).one()
        print("\n=== GATE PASS/NEAR 24h ===")
        print(dict(near._mapping))

        print("\n=== TELEGRAM CHATS IN DB ===")
        try:
            chats = (
                await session.execute(
                    text(
                        """
                        SELECT id, telegram_chat_id, is_active, created_at
                        FROM telegram_chats
                        ORDER BY id
                        LIMIT 20
                        """
                    )
                )
            ).all()
            for r in chats:
                print(" ", tuple(r))
            if not chats:
                issues.append("no_telegram_chats_rows")
        except Exception as exc:
            print(" ", exc)
            issues.append(f"telegram_chats_query_failed:{exc}")

        print("\n=== PAPER OPEN FROM LAST DISPATCHED? ===")
        paper = (
            await session.execute(
                text(
                    """
                    SELECT COUNT(*) FILTER (WHERE opened_at > NOW() - INTERVAL '48 hours') AS opened_48h,
                           MAX(opened_at) AS last_opened
                    FROM paper_positions
                    WHERE status IN ('open','closed','pending')
                    """
                )
            )
        ).one()
        print(dict(paper._mapping))

    # Bot API getMe (proves token works)
    print("\n=== TELEGRAM getMe ===")
    try:
        import httpx

        token = s.telegram_bot_token
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
            ok = bool(data.get("ok"))
            print(f"  http={r.status_code} ok={ok} username={data.get('result', {}).get('username')}")
            if not ok:
                issues.append(f"telegram_getMe_failed:{data}")
    except Exception as exc:
        print(f"  FAIL {exc}")
        issues.append(f"telegram_getMe_error:{exc}")

    print("\n=== SUMMARY ===")
    print(f"issues={len(issues)}")
    for i in issues:
        print(" -", i)
    # Dispatch "works" if config+token+chats OK and suppression is only filter-driven
    plumbing_ok = not any(
        x.startswith("telegram_") or x.startswith("no_telegram") for x in issues
    )
    print(f"DISPATCH_PLUMBING_OK={plumbing_ok}")
    await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
