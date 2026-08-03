"""Deeper dispatch audit: chat IDs, paper-gated telegram, short_pass fate."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.container import build_container
from app.core.logging import configure_logging
from app.database.session import session_scope


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    c = build_container()
    s = c.settings

    print("=== CHAT ID PARSING ===")
    allowed = s.telegram_allowed_chat_ids
    admin = s.telegram_admin_chat_ids
    print("type_allowed", type(allowed), "repr", repr(allowed)[:200])
    print("type_admin", type(admin), "repr", repr(admin)[:200])
    # normalized accessors used by bot
    for name in (
        "parsed_telegram_allowed_chat_ids",
        "telegram_allowed_chat_id_list",
        "allowed_telegram_chat_ids",
    ):
        if hasattr(s, name):
            print(name, getattr(s, name))

    print("\n=== CONTAINER WIRING ===")
    print("has scan_service", hasattr(c, "scan_service"), getattr(c, "scan_service", None))
    # worker builds scan differently
    from app.services.scan_service import ScanService

    scan = None
    for attr in dir(c):
        obj = getattr(c, attr, None)
        if isinstance(obj, ScanService):
            scan = obj
            print("found ScanService on", attr)
            print("  paper", type(obj._paper).__name__ if obj._paper else None)
            print("  dispatcher", type(obj._dispatcher).__name__ if obj._dispatcher else None)
    if scan is None:
        print("ScanService not on container; checking build path in cli worker...")

    print("telegram_signal_dispatch", s.telegram_signal_dispatch)
    print("enable_paper", s.enable_paper_trading)

    async with session_scope() as session:
        cols = (
            await session.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='telegram_chats' ORDER BY 1
                    """
                )
            )
        ).scalars().all()
        print("\ntelegram_chats cols", list(cols))
        rows = (await session.execute(text("SELECT * FROM telegram_chats LIMIT 10"))).mappings().all()
        for r in rows:
            print(dict(r))

        print("\n=== SHORT_PASS LAST 24h WITHOUT PAPER OPEN ===")
        # shorts with score<=25
        q = await session.execute(
            text(
                """
                WITH pass AS (
                  SELECT s.id, a.symbol, s.direction, s.score, s.created_at
                  FROM signals s
                  JOIN assets a ON a.id = s.asset_id
                  WHERE s.created_at > NOW() - INTERVAL '24 hours'
                    AND s.direction IN ('SHORT','STRONG_SHORT')
                    AND s.score <= 25
                )
                SELECT COUNT(*) AS pass_n,
                       COUNT(p.id) AS with_paper,
                       COUNT(*) FILTER (WHERE d.status='sent') AS sent_deliveries,
                       COUNT(*) FILTER (WHERE d.status='suppressed') AS suppressed_deliveries
                FROM pass
                LEFT JOIN paper_positions p ON p.signal_id = pass.id
                LEFT JOIN signal_deliveries d ON d.signal_id = pass.id
                """
            )
        )
        print(dict(q.mappings().one()))

        print("\n=== SAMPLE SHORT_PASS + DELIVERY REASON ===")
        samples = (
            await session.execute(
                text(
                    """
                    SELECT a.symbol, s.direction, ROUND(s.score::numeric,1), s.created_at,
                           d.status, d.suppression_reason,
                           p.status AS paper_status, p.id AS paper_id
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    LEFT JOIN LATERAL (
                      SELECT status, suppression_reason
                      FROM signal_deliveries
                      WHERE signal_id = s.id
                      ORDER BY id DESC LIMIT 1
                    ) d ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT id, status FROM paper_positions
                      WHERE signal_id = s.id
                      ORDER BY id DESC LIMIT 1
                    ) p ON TRUE
                    WHERE s.created_at > NOW() - INTERVAL '24 hours'
                      AND s.direction IN ('SHORT','STRONG_SHORT')
                      AND s.score <= 25
                    ORDER BY s.created_at DESC
                    LIMIT 25
                    """
                )
            )
        ).all()
        for r in samples:
            print(tuple(r))

        print("\n=== PAPER NOTIFIER / OPEN PATH LOG PROXY ===")
        print("last paper open:", (
            await session.execute(
                text("SELECT MAX(opened_at), COUNT(*) FILTER (WHERE opened_at > NOW()-INTERVAL '7 days') FROM paper_positions")
            )
        ).one())

    # Telegram getMe
    import httpx

    print("\n=== TELEGRAM getMe ===")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe")
        data = r.json()
        print("ok", data.get("ok"), "username", (data.get("result") or {}).get("username"))

    await c.aclose()


if __name__ == "__main__":
    asyncio.run(main())
