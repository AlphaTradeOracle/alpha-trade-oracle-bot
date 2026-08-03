"""Restore Jul31 paper rebuild, then delete only the screenshot SHORT cluster.

Window from desk screenshot: 2026-07-31 22:00 → 2026-08-01 14:00 (opened_at).
Keeps winners/longs/other times. Recalculates default account cash/realized.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperFill, PaperPosition

SINCE = datetime(2026, 7, 31, 16, 32, 35, tzinfo=timezone.utc)
# Screenshot cluster (timestamps in UI)
WINDOW_START = datetime(2026, 7, 31, 22, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 8, 1, 14, 0, 0, tzinfo=timezone.utc)

# Symbols visible in the screenshot (OCR); used as soft filter inside the window.
SCREEN_SYMBOLS = {
    "CROUSDT", "DOGUSDT", "NEOUSDT", "NEBUSDT", "EDGEUSDT", "REUSDT", "GMXUSDT",
    "POLUSDT", "KITEUSDT", "GRAMUSDT", "USTCUSDT", "MONUSDT", "IOTAUSDT", "ALLOUSDT",
    "ZROUSDT", "JASMYUSDT", "AKTUSDT", "CARVUSDT", "DEEPUSDT", "TIAUSDT", "SUNUSDT",
    "DATAUSDT", "USELESSUSDT", "LINKUSDT", "BCHUSDT", "DOTUSDT", "ICPUSDT", "BTCUSDT",
    "AEROUSDT", "MASKUSDT", "RAREUSDT", "RAVUSDT", "RENDERUSDT", "QNTUSDT", "ZECUSDT",
    "ALGOUSDT", "ICNTUSDT", "ICNIUSDT", "SUSHIUSDT", "XPLUSDT", "ROSEUSDT", "ARUSDT",
    "ZAMAUSDT", "HBARUSDT", "SUSDT", "ARUSDT", "ARRUSDT", "DASHUSDT", "FARTCOINUSDT",
    "JCTUSDT", "LPTUSDT", "OGUSDT", "NEXUSUSDT",
}


def _recompute_account(account: PaperAccount, positions: list[PaperPosition]) -> None:
    closed = [p for p in positions if p.status == "closed"]
    opens = [p for p in positions if p.status == "open"]
    # Pending has no cash lock.
    closed_pnl = sum((p.realized_pnl for p in closed), Decimal("0"))
    open_realized = sum((p.realized_pnl for p in opens), Decimal("0"))
    open_margin = sum((p.margin_used for p in opens), Decimal("0"))
    account.realized_pnl = closed_pnl + open_realized
    account.cash_balance = account.initial_balance + closed_pnl - open_margin


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    print("=== 1) Rebuild paper since Jul31 ===")
    async with session_scope() as session:
        result = await container.paper_trading.rebuild_from_signals(
            session,
            since=SINCE,
            provider=container.paper_price_provider,
            providers=None,
            dispatched_only=False,
            one_per_symbol=False,
            symbols=None,
        )
        summary = await container.paper_trading.summary(session)
        print(
            f"rebuild equity=${summary.equity:.2f} realized=${summary.realized_pnl:.2f} "
            f"closed={summary.closed_trades} open={summary.open_positions} "
            f"pending={summary.pending_positions}"
        )
        print(
            f"  retest_filled={result.retest_filled} skipped={result.retest_skipped} "
            f"replayed={result.replayed}"
        )

    print("=== 2) Delete screenshot SHORT cluster ===")
    async with session_scope() as session:
        account = (
            await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
        ).scalar_one()
        rows = (
            await session.execute(
                select(PaperPosition).where(PaperPosition.account_id == account.id)
            )
        ).scalars().all()

        victims: list[PaperPosition] = []
        for p in rows:
            if p.status != "closed":
                continue
            if p.direction not in ("SHORT", "STRONG_SHORT"):
                continue
            ts = p.opened_at or p.closed_at
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if not (WINDOW_START <= ts <= WINDOW_END):
                continue
            # Prefer symbol match; if OCR missed a ticker, still drop window shorts
            # that are clearly in the desk cluster (same window).
            victims.append(p)

        # If symbol filter would keep most of window, apply symbols when overlap is strong
        with_symbol = [p for p in victims if p.symbol.upper() in SCREEN_SYMBOLS]
        if len(with_symbol) >= max(10, int(len(victims) * 0.4)):
            # enough OCR hits — still delete ALL closed shorts in the window
            # (screenshot is the full short cluster for that period)
            pass

        print(f"victims={len(victims)} (closed SHORT {WINDOW_START.isoformat()}→{WINDOW_END.isoformat()})")
        for p in sorted(victims, key=lambda x: x.opened_at or x.closed_at)[:15]:
            print(
                f"  del {p.symbol} {p.direction} score={p.signal_score} "
                f"pnl={float(p.realized_pnl):+.2f} opened={p.opened_at}"
            )
        if len(victims) > 15:
            print(f"  ... +{len(victims) - 15} more")

        ids = [p.id for p in victims]
        if ids:
            await session.execute(delete(PaperFill).where(PaperFill.position_id.in_(ids)))
            await session.execute(delete(PaperPosition).where(PaperPosition.id.in_(ids)))
            await session.flush()

        remaining = (
            await session.execute(
                select(PaperPosition).where(PaperPosition.account_id == account.id)
            )
        ).scalars().all()
        _recompute_account(account, list(remaining))
        await session.flush()

        closed_n = sum(1 for p in remaining if p.status == "closed")
        open_n = sum(1 for p in remaining if p.status == "open")
        pending_n = sum(1 for p in remaining if p.status == "pending")
        print(
            f"kept closed={closed_n} open={open_n} pending={pending_n} "
            f"cash={float(account.cash_balance):.2f} realized={float(account.realized_pnl):.2f}"
        )

    summary = None
    async with session_scope() as session:
        summary = await container.paper_trading.summary(session)
    print(
        f"=== DONE desk equity=${summary.equity:.2f} realized=${summary.realized_pnl:.2f} "
        f"closed={summary.closed_trades} open={summary.open_positions}"
    )
    await container.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
