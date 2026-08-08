"""Rollback combo_active apply: un-revive signals, restore paper balances.

Restores account ``default`` cash/realized to the pre-apply snapshot
(cash=$5114.257, realized=$114.257, no open/pending). Closed-trade row for
CCUSDT cannot be perfectly reconstructed after ledger wipe — balances are
restored exactly; position history for that one closed trade is cleared.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.database.session import session_scope
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository

_REVIVE_NOTE = "revived_combo_active"
# Snapshot from /tmp/combo_active_apply.json before first rebuild
_PRE_CASH = Decimal("5114.25700054")
_PRE_REALIZED = Decimal("114.25700054")


async def _unrevive(session) -> dict:
    result = await session.execute(
        select(Signal).where(
            Signal.invalidation_note.is_not(None),
            Signal.invalidation_note.contains(_REVIVE_NOTE),
        )
    )
    n = 0
    by = {"STRONG_LONG": 0, "STRONG_SHORT": 0}
    for signal in result.scalars():
        note = signal.invalidation_note or ""
        orig = note
        if orig.startswith(_REVIVE_NOTE):
            orig = orig.split(":", 1)[-1].strip()
            if " | " in orig:
                # keep only the revived reason prefix, drop older suffix
                orig = orig.split(" | ", 1)[0].strip()
        prev = signal.direction
        signal.direction = SignalDirection.NO_TRADE.value
        signal.no_trade_reason = orig or signal.no_trade_reason
        # drop revive stamp; keep any prior note after " | "
        if " | " in note:
            signal.invalidation_note = note.split(" | ", 1)[1].strip() or None
        else:
            signal.invalidation_note = orig
        n += 1
        by[prev] = by.get(prev, 0) + 1
    return {"unrevived": n, "from_direction": by}


async def _restore_paper(session) -> dict:
    settings = get_settings()
    repo = PaperRepository(session)
    account = await repo.get_or_create_account(
        name="default",
        initial_balance=Decimal(str(settings.paper_initial_balance)),
        margin_per_trade=Decimal(str(settings.paper_margin_per_trade)),
        leverage=float(settings.paper_leverage),
    )
    deleted = await repo.reset_ledger(account)
    account.cash_balance = _PRE_CASH
    account.realized_pnl = _PRE_REALIZED
    await session.flush()
    return {
        "deleted_positions": deleted,
        "cash_balance": float(account.cash_balance),
        "realized_pnl": float(account.realized_pnl),
        "initial_balance": float(account.initial_balance),
    }


async def _counts(session) -> dict:
    row = (
        await session.execute(
            text(
                """
                select
                  (select count(*) from signals
                     where invalidation_note like :pat) as still_revive_notes,
                  (select count(*) from paper_positions p
                     join paper_accounts a on a.id=p.account_id
                     where a.name='default') as positions,
                  (select cash_balance from paper_accounts where name='default') as cash,
                  (select realized_pnl from paper_accounts where name='default') as realized
                """
            ),
            {"pat": f"%{_REVIVE_NOTE}%"},
        )
    ).mappings().one()
    return {
        "still_revive_notes": int(row["still_revive_notes"]),
        "positions": int(row["positions"]),
        "cash": float(row["cash"]),
        "realized": float(row["realized"]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/tmp/combo_active_rollback.json")
    args = parser.parse_args()

    configure_logging("INFO", json_output=False)
    settings = get_settings()
    print(
        "gates "
        f"L>={settings.signal_min_score} RSI>={settings.signal_rsi_short_min} "
        f"ADX>={settings.signal_min_adx} pend×{settings.paper_retest_pending_multiplier}",
        flush=True,
    )

    async with session_scope() as session:
        unrev = await _unrevive(session)
        await session.flush()
        print(f"unrevive {unrev}", flush=True)
        paper = await _restore_paper(session)
        await session.flush()
        print(f"paper {paper}", flush=True)
        verify = await _counts(session)

    out = {
        "at": utc_now().isoformat(),
        "gates": {
            "long_min": float(settings.signal_min_score),
            "rsi_short_min": float(settings.signal_rsi_short_min),
            "adx_min": float(settings.signal_min_adx),
            "pending_mult": int(settings.paper_retest_pending_multiplier),
        },
        "unrevive": unrev,
        "paper": paper,
        "verify": verify,
        "note": (
            "Balances restored to pre-combo_active snapshot. "
            "CCUSDT closed-trade row was wiped by earlier rebuild and is not re-inserted."
        ),
    }
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"WROTE {args.out}", flush=True)
    print("VERIFY", verify, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
