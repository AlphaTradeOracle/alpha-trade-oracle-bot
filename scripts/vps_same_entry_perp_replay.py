"""Same-entry spot vs perp equity comparison.

1) Rebuild with SPOT candles + live symbol filter (same as paper_reset_symbols).
2) Freeze filled entries (open+closed).
3) Reset ledger, re-seed those exact entries, replay exits with PERP candles.
4) Print side-by-side equity / trade counts.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.container import build_container
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.market_data.factory import create_market_data_provider
from app.models.paper import PaperFill, PaperPosition
from app.repositories.paper_repository import PaperRepository
from app.scheduler.jobs import _collect_prices

logger = get_logger(__name__)


@dataclass
class FrozenEntry:
    signal_id: int | None
    asset_id: int | None
    symbol: str
    direction: str
    timeframe: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    take_profit_3: Decimal
    initial_quantity: Decimal
    margin_used: Decimal
    notional: Decimal
    leverage: float
    risk_amount: Decimal
    signal_score: float | None
    opened_at: datetime
    expires_at: datetime | None
    spot_status: str
    spot_exit_reason: str | None
    spot_realized: Decimal


def _load_symbols(path: str) -> set[str]:
    out: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line.upper())
    return out


def _fmt_summary(label: str, summary, *, filled: int | None = None) -> None:
    ret = 0.0
    if summary.initial_balance:
        ret = (summary.equity / summary.initial_balance - 1.0) * 100.0
    print(f"=== {label} ===")
    print(f"equity={summary.equity:.2f}")
    print(f"cash={summary.cash_balance:.2f}")
    print(f"realized_pnl={summary.realized_pnl:.2f}")
    print(f"open_positions={summary.open_positions}")
    print(f"pending_positions={summary.pending_positions}")
    print(f"closed_trades={summary.closed_trades}")
    if filled is not None:
        print(f"filled_entries={filled}")
    print(f"win_rate={summary.win_rate:.1f}")
    print(f"total_return_pct={ret:.2f}")


async def _freeze_filled(session) -> list[FrozenEntry]:
    account = await PaperRepository(session).get_or_create_account(
        name="default",
        initial_balance=Decimal("5000"),
        margin_per_trade=Decimal("100"),
        leverage=10.0,
    )
    positions = await PaperRepository(session).list_positions(account.id)
    frozen: list[FrozenEntry] = []
    for p in positions:
        if p.status not in ("open", "closed"):
            continue
        frozen.append(
            FrozenEntry(
                signal_id=p.signal_id,
                asset_id=p.asset_id,
                symbol=p.symbol,
                direction=p.direction,
                timeframe=p.timeframe or "1h",
                entry_price=Decimal(str(p.entry_price)),
                stop_loss=Decimal(str(p.stop_loss)),
                take_profit_1=Decimal(str(p.take_profit_1)),
                take_profit_2=Decimal(str(p.take_profit_2)),
                take_profit_3=Decimal(str(p.take_profit_3)),
                initial_quantity=Decimal(str(p.initial_quantity)),
                margin_used=Decimal(str(p.margin_used)),
                notional=Decimal(str(p.notional)),
                leverage=float(p.leverage),
                risk_amount=Decimal(str(p.risk_amount or 0)),
                signal_score=float(p.signal_score) if p.signal_score is not None else None,
                opened_at=ensure_utc(p.opened_at),
                expires_at=ensure_utc(p.expires_at) if p.expires_at else None,
                spot_status=p.status,
                spot_exit_reason=p.exit_reason,
                spot_realized=Decimal(str(p.realized_pnl or 0)),
            )
        )
    frozen.sort(key=lambda e: e.opened_at)
    return frozen


async def _seed_and_replay_perp(
    session,
    paper,
    perp_provider,
    frozen: list[FrozenEntry],
    fee_percent: float,
) -> None:
    account = await paper.get_or_create_account(session)
    repo = PaperRepository(session)
    await repo.reset_ledger(account)
    fee_rate = Decimal(str(fee_percent)) / Decimal("100")
    cutoff = utc_now()

    with paper._without_notifications():
        for entry in frozen:
            entry_fee = entry.notional * fee_rate
            cash_needed = entry.margin_used + entry_fee
            if account.cash_balance < cash_needed:
                logger.warning(
                    "same_entry_skip_cash",
                    symbol=entry.symbol,
                    cash=float(account.cash_balance),
                    needed=float(cash_needed),
                )
                continue

            account.cash_balance -= cash_needed
            account.realized_pnl -= entry_fee

            position = PaperPosition(
                account_id=account.id,
                signal_id=entry.signal_id,
                asset_id=entry.asset_id,
                symbol=entry.symbol,
                direction=entry.direction,
                status="open",
                timeframe=entry.timeframe,
                entry_price=entry.entry_price,
                stop_loss=entry.stop_loss,
                current_stop=entry.stop_loss,
                take_profit_1=entry.take_profit_1,
                take_profit_2=entry.take_profit_2,
                take_profit_3=entry.take_profit_3,
                initial_quantity=entry.initial_quantity,
                remaining_quantity=entry.initial_quantity,
                margin_used=entry.margin_used,
                notional=entry.notional,
                leverage=entry.leverage,
                fees=entry_fee,
                realized_pnl=-entry_fee,
                risk_amount=entry.risk_amount,
                signal_score=entry.signal_score,
                opened_at=entry.opened_at,
                expires_at=entry.expires_at,
                peak_price=entry.entry_price,
            )
            await repo.add_position(position)
            await repo.add_fill(
                PaperFill(
                    position_id=position.id,
                    reason="entry",
                    price=entry.entry_price,
                    quantity=entry.initial_quantity,
                    fee=entry_fee,
                    pnl=-entry_fee,
                    filled_at=entry.opened_at,
                )
            )

            try:
                series = await perp_provider.get_candles(
                    entry.symbol,
                    entry.timeframe,
                    limit=100_000,
                    start_time=entry.opened_at,
                    end_time=cutoff,
                )
                await paper._replay_bars(session, account, position, series.candles)
            except Exception as exc:
                logger.warning(
                    "same_entry_perp_replay_failed",
                    symbol=entry.symbol,
                    error=str(exc),
                )

        still_open = await repo.list_open_positions(account.id)
        if still_open:
            symbols = [p.symbol for p in still_open]
            prices = await _collect_prices(perp_provider, symbols, providers=None)
            await paper.update_open_positions(session, prices)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-07-31T16:32:35+00:00")
    parser.add_argument("--symbols-file", default="/tmp/paper_reset_symbols.txt")
    parser.add_argument("--dispatched-only", action="store_true")
    args = parser.parse_args()

    configure_logging("INFO", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    symbols = _load_symbols(args.symbols_file)
    print(f"since={since.isoformat()} symbols={len(symbols)}")

    container = build_container()
    spot = create_market_data_provider(container.settings)
    perp = container.paper_price_provider
    paper = container.paper_trading
    fee_pct = float(container.settings.paper_fee_percent)

    try:
        # --- SPOT baseline (same entry universe as live rebuild) ---
        async with session_scope() as session:
            result = await paper.rebuild_from_signals(
                session,
                since=since,
                provider=spot,
                providers=None,
                dispatched_only=args.dispatched_only,
                one_per_symbol=False,
                symbols=symbols,
            )
            spot_summary = await paper.summary(session)
            frozen = await _freeze_filled(session)
            print(
                f"SPOT rebuild: opened={result.backfill.opened if result.backfill else 0} "
                f"retest_filled={result.retest_filled} retest_skipped={result.retest_skipped} "
                f"replayed={result.replayed} still_open={result.still_open}"
            )
            _fmt_summary("SPOT_BOOK", spot_summary, filled=len(frozen))
            print("SPOT_FILLS")
            for e in frozen:
                print(
                    f"  {e.opened_at.isoformat()} {e.symbol} {e.direction} "
                    f"entry={e.entry_price} spot={e.spot_status}/{e.spot_exit_reason} "
                    f"rpnl={e.spot_realized}"
                )

        # --- PERP replay of identical entries ---
        async with session_scope() as session:
            await _seed_and_replay_perp(session, paper, perp, frozen, fee_pct)
            perp_summary = await paper.summary(session)
            _fmt_summary("PERP_SAME_ENTRY", perp_summary, filled=len(frozen))

            # Exit reason deltas for closed trades
            account = await paper.get_or_create_account(session)
            positions = await PaperRepository(session).list_positions(account.id)
            by_key = {
                (p.signal_id, p.symbol, ensure_utc(p.opened_at).isoformat()): p
                for p in positions
            }
            print("EXIT_COMPARE")
            for e in frozen:
                key = (e.signal_id, e.symbol, e.opened_at.isoformat())
                p = by_key.get(key)
                if p is None:
                    print(f"  MISSING {e.symbol} {e.opened_at.isoformat()}")
                    continue
                print(
                    f"  {e.symbol}: spot={e.spot_status}/{e.spot_exit_reason}"
                    f"({float(e.spot_realized):+.2f}) -> "
                    f"perp={p.status}/{p.exit_reason}"
                    f"({float(p.realized_pnl or 0):+.2f})"
                )

            delta = perp_summary.equity - spot_summary.equity
            print("=== DELTA ===")
            print(f"equity_delta={delta:.2f}")
            print(f"realized_delta={perp_summary.realized_pnl - spot_summary.realized_pnl:.2f}")
            print(f"filled_count={len(frozen)}")
    finally:
        await container.aclose()
        close = getattr(spot, "aclose", None)
        if callable(close):
            await close()


if __name__ == "__main__":
    asyncio.run(main())
