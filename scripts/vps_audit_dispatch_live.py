"""Live-only dispatch health (exclude rebuild-stamped batches)."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import text

from app.container import build_container
from app.core.logging import configure_logging
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    c = build_container()
    s = c.settings

    print("=== PLUMBING ===")
    print("telegram_signal_dispatch", s.telegram_signal_dispatch)
    print("enable_paper_trading", s.enable_paper_trading)
    print("chats_allowed", sorted(s.allowed_chat_ids))
    token = s.telegram_bot_token
    if hasattr(token, "get_secret_value"):
        token = token.get_secret_value()
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    print("telegram_getMe", r.json().get("ok"), (r.json().get("result") or {}).get("username"))

    async with session_scope() as session:
        print("\n=== LIVE SCANS TODAY: gate-pass shorts (score 18-25) ===")
        # Distinct created_at second buckets to ignore identical-stamp rebuild rows
        rows = (
            await session.execute(
                text(
                    """
                    SELECT date_trunc('minute', created_at) AS minute,
                           COUNT(*) AS n,
                           COUNT(*) FILTER (WHERE direction IN ('SHORT','STRONG_SHORT') AND score <= 25 AND score > 18) AS short_pass,
                           COUNT(*) FILTER (WHERE direction IN ('LONG','STRONG_LONG') AND score >= 75) AS long_pass
                    FROM signals
                    WHERE created_at > NOW() - INTERVAL '12 hours'
                    GROUP BY 1
                    HAVING COUNT(*) < 50  -- live scan writes spread out; rebuild dumps hundreds same second
                    ORDER BY 1 DESC
                    LIMIT 30
                    """
                )
            )
        ).all()
        for row in rows:
            print(tuple(row))

        print("\n=== SHORT_PASS LIVE LAST 12h (spread created_at) ===")
        live_pass = (
            await session.execute(
                text(
                    """
                    SELECT a.symbol, s.direction, ROUND(s.score::numeric,1), s.created_at,
                           d.suppression_reason, p.status AS paper_status
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    LEFT JOIN LATERAL (
                      SELECT suppression_reason FROM signal_deliveries
                      WHERE signal_id = s.id ORDER BY id DESC LIMIT 1
                    ) d ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT status FROM paper_positions WHERE signal_id = s.id
                      ORDER BY id DESC LIMIT 1
                    ) p ON TRUE
                    WHERE s.created_at > NOW() - INTERVAL '12 hours'
                      AND s.direction IN ('SHORT','STRONG_SHORT')
                      AND s.score <= 25 AND s.score > 18
                      AND NOT EXISTS (
                        SELECT 1 FROM signals s2
                        WHERE s2.created_at = s.created_at
                        HAVING COUNT(*) > 20
                      )
                    ORDER BY s.created_at DESC
                    LIMIT 30
                    """
                )
            )
        ).all()
        print("count_shown", len(live_pass))
        for row in live_pass:
            print(tuple(row))

        print("\n=== LAST SUCCESSFUL SENT + PAPER OPEN PATH ===")
        print(
            (
                await session.execute(
                    text(
                        """
                        SELECT MAX(sent_at) FILTER (WHERE status='sent') AS last_sent,
                               MAX(opened_at) AS last_paper_open
                        FROM signal_deliveries d
                        FULL OUTER JOIN paper_positions p ON TRUE
                        """
                    )
                )
            ).one()
        )
        # cleaner
        last_sent = (
            await session.execute(
                text("SELECT MAX(sent_at) FROM signal_deliveries WHERE status='sent'")
            )
        ).scalar()
        last_paper = (
            await session.execute(text("SELECT MAX(opened_at) FROM paper_positions"))
        ).scalar()
        print("last_sent", last_sent)
        print("last_paper_open", last_paper)

        print("\n=== ACTIVE CHATS ===")
        chats = (
            await session.execute(
                text(
                    """
                    SELECT id, chat_id, is_active, notifications_enabled, is_admin
                    FROM telegram_chats
                    """
                )
            )
        ).all()
        for row in chats:
            print(tuple(row))

    print("\n=== VERDICT ===")
    print(
        "Dispatch wiring OK if: flag on, getMe ok, chats active, paper notifier set when paper on."
    )
    print(
        "With paper trading ON: Telegram only after paper IST entry (not pending retest)."
    )
    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
