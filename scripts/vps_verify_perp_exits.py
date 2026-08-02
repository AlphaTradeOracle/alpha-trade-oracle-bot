"""Full audit: current paper book vs perpetual OHLC + accounting.

Verifies for every filled position (open/closed):
- fill prices match TP/SL/entry levels
- independent 1h OHLC replay on perp reproduces exit reason / fill sequence
- account cash/margin/realized identity
- venue routing present for every symbol
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.container import build_container
from app.core.enums import ExitReason, SignalDirection
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperFill, PaperPosition
from app.signals.risk import RiskManager

logger = get_logger(__name__)

TP_REASONS = {
    ExitReason.TAKE_PROFIT_1.value,
    ExitReason.TAKE_PROFIT_2.value,
    ExitReason.TAKE_PROFIT_3.value,
}


@dataclass
class SimFill:
    reason: str
    price: float
    when: datetime


@dataclass
class SimResult:
    status: str
    exit_reason: str | None
    fills: list[SimFill] = field(default_factory=list)
    tp1: bool = False
    tp2: bool = False
    tp3: bool = False
    current_stop: float = 0.0
    bars: int = 0
    venue: str | None = None
    error: str | None = None


def _rel_close(a: float, b: float, tol: float = 1e-5) -> bool:
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale <= tol


def _simulate_perp_replay(
    *,
    is_long: bool,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    opened_at: datetime,
    expires_at: datetime | None,
    qty0: float,
    fractions: tuple[float, float, float],
    fee_pct: float,
    move_be: bool,
    expiry_mult_after_tp1: int,
    early_hours: int,
    early_mfe_r: float,
    candles,
    timeframe_hours: float = 1.0,
) -> SimResult:
    """Mirror paper_trading_service._replay_bars + scale-out/BE/early-scratch."""
    rem = qty0
    cur_stop = stop
    peak = entry
    t1 = t2 = t3 = False
    fills: list[SimFill] = []
    status = "open"
    exit_reason: str | None = None
    exp = expires_at

    def mfe_r() -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        if is_long:
            return (peak - entry) / risk
        return (entry - peak) / risk

    for candle in candles:
        when = ensure_utc(candle.open_time)
        if when < opened_at:
            continue
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)

        # Stop first
        stop_hit = low <= cur_stop if is_long else high >= cur_stop
        if stop_hit:
            fills.append(SimFill(ExitReason.STOP_LOSS.value, cur_stop, when))
            status = "closed"
            exit_reason = ExitReason.STOP_LOSS.value
            rem = 0.0
            break

        # Peak / early scratch
        fav = high if is_long else low
        if is_long:
            peak = max(peak, fav)
        else:
            peak = min(peak, fav)

        if early_hours > 0 and not t1:
            elapsed_h = (when - opened_at).total_seconds() / 3600.0
            if elapsed_h >= early_hours and mfe_r() < early_mfe_r:
                fills.append(SimFill(ExitReason.EARLY_SCRATCH.value, close, when))
                status = "closed"
                exit_reason = ExitReason.EARLY_SCRATCH.value
                rem = 0.0
                break

        # TPs on favorable extreme (same as _apply_price with check_stop=False)
        price = high if is_long else low
        levels = (
            (not t1, tp1, ExitReason.TAKE_PROFIT_1.value, fractions[0], 1),
            (not t2, tp2, ExitReason.TAKE_PROFIT_2.value, fractions[1], 2),
            (not t3, tp3, ExitReason.TAKE_PROFIT_3.value, fractions[2], 3),
        )
        for pending, tp, reason, frac, level in levels:
            if not pending:
                continue
            hit = price >= tp if is_long else price <= tp
            if not hit:
                break
            qty = min(qty0 * frac, rem)
            if level == 3:
                qty = rem
            fills.append(SimFill(reason, tp, when))
            rem -= qty
            if level == 1:
                t1 = True
                if move_be:
                    cur_stop = RiskManager.fee_aware_breakeven(
                        entry, is_long=is_long, fee_percent=fee_pct
                    )
                # Keep DB/frozen expires_at — do not re-extend (seed already has post-TP1 expiry).
            elif level == 2:
                t2 = True
            else:
                t3 = True
            if rem <= 1e-12:
                status = "closed"
                exit_reason = reason
                rem = 0.0
                break
        if status == "closed":
            break

        if exp is not None and when >= exp and rem > 0:
            fills.append(SimFill(ExitReason.EXPIRED.value, close, when))
            status = "closed"
            exit_reason = ExitReason.EXPIRED.value
            rem = 0.0
            break

    return SimResult(
        status=status,
        exit_reason=exit_reason,
        fills=fills,
        tp1=t1,
        tp2=t2,
        tp3=t3,
        current_stop=cur_stop,
        bars=len([c for c in candles if ensure_utc(c.open_time) >= opened_at]),
    )


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    container = build_container()
    settings = container.settings
    router = container.paper_price_provider
    fracs = settings.parsed_scale_out_fractions
    fee_pct = float(settings.paper_fee_percent)
    issues: list[str] = []

    try:
        async with session_scope() as session:
            account = (
                await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
            ).scalar_one()
            positions = list(
                (
                    await session.execute(
                        select(PaperPosition)
                        .where(PaperPosition.account_id == account.id)
                        .options(selectinload(PaperPosition.fills))
                        .order_by(PaperPosition.opened_at.asc())
                    )
                ).scalars()
            )

            open_pos = [p for p in positions if p.status == "open"]
            closed_pos = [p for p in positions if p.status == "closed"]
            pending_pos = [p for p in positions if p.status == "pending"]
            cancelled = [
                p for p in positions if p.status not in ("open", "closed", "pending")
            ]

            open_margin = sum(float(p.margin_used) for p in open_pos)
            pos_realized_sum = sum(float(p.realized_pnl or 0) for p in positions)
            cash = float(account.cash_balance)
            acct_realized = float(account.realized_pnl)
            initial = float(account.initial_balance)
            equity_no_upnl = cash + open_margin

            print("=== BOOK ===")
            print(f"positions_total={len(positions)}")
            print(f"open={len(open_pos)} closed={len(closed_pos)} pending={len(pending_pos)} other={len(cancelled)}")
            print(f"cash={cash:.4f} open_margin={open_margin:.4f} equity_no_upnl={equity_no_upnl:.4f}")
            print(f"account.realized_pnl={acct_realized:.4f}")
            print(f"sum(position.realized_pnl)={pos_realized_sum:.4f}")
            print(f"initial={initial:.4f}")

            # Identity: cash + open_margin == initial + account.realized
            identity = initial + acct_realized
            if abs(equity_no_upnl - identity) > 0.05:
                issues.append(
                    f"ACCOUNT_IDENTITY cash+margin={equity_no_upnl:.4f} != initial+realized={identity:.4f}"
                )
            else:
                print("account_identity=OK")

            if abs(pos_realized_sum - acct_realized) > 0.05:
                # cancelled/pending can hold entry fees; flag if large
                issues.append(
                    f"REALIZED_MISMATCH positions_sum={pos_realized_sum:.4f} account={acct_realized:.4f}"
                )
            else:
                print("realized_sum=OK")

            print("=== PER_TRADE PERP REPLAY ===")
            seq_ok = 0
            price_ok = 0
            price_bad = 0
            seq_bad = 0
            venue_ok = 0

            for pos in open_pos + closed_pos:
                is_long = SignalDirection(pos.direction).is_long
                opened = ensure_utc(pos.opened_at)
                end = ensure_utc(pos.closed_at) if pos.closed_at else utc_now()
                tf = pos.timeframe or "1h"

                # Fill price level checks
                fills = sorted(pos.fills, key=lambda f: ensure_utc(f.filled_at))
                for f in fills:
                    reason = f.reason
                    px = float(f.price)
                    expected: float | None = None
                    if reason == "entry":
                        expected = float(pos.entry_price)
                    elif reason == ExitReason.TAKE_PROFIT_1.value:
                        expected = float(pos.take_profit_1)
                    elif reason == ExitReason.TAKE_PROFIT_2.value:
                        expected = float(pos.take_profit_2)
                    elif reason == ExitReason.TAKE_PROFIT_3.value:
                        expected = float(pos.take_profit_3)
                    elif reason == ExitReason.STOP_LOSS.value:
                        # fill must equal whatever stop was used at exit
                        expected = px  # tautology; checked via sim
                    if expected is not None and reason != ExitReason.STOP_LOSS.value:
                        if _rel_close(px, expected):
                            price_ok += 1
                        else:
                            price_bad += 1
                            msg = (
                                f"FILL_PRICE {pos.symbol} {reason}: fill={px} expected={expected}"
                            )
                            issues.append(msg)
                            print("  FAIL", msg)

                try:
                    venue = await router.resolve_venue(pos.symbol)
                    venue_name = venue.name
                    venue_ok += 1
                except Exception as exc:
                    issues.append(f"VENUE {pos.symbol}: {exc}")
                    venue_name = None
                    print(f"  FAIL VENUE {pos.symbol}: {exc}")
                    continue

                try:
                    series = await router.get_candles(
                        pos.symbol,
                        tf,
                        limit=100_000,
                        start_time=opened - timedelta(hours=2),
                        end_time=end + timedelta(hours=2),
                    )
                    candles = list(series.candles) if series and not series.is_empty else []
                except Exception as exc:
                    issues.append(f"CANDLES {pos.symbol}: {exc}")
                    print(f"  FAIL CANDLES {pos.symbol}: {exc}")
                    continue

                sim = _simulate_perp_replay(
                    is_long=is_long,
                    entry=float(pos.entry_price),
                    stop=float(pos.stop_loss),
                    tp1=float(pos.take_profit_1),
                    tp2=float(pos.take_profit_2),
                    tp3=float(pos.take_profit_3),
                    opened_at=opened,
                    expires_at=ensure_utc(pos.expires_at) if pos.expires_at else None,
                    qty0=float(pos.initial_quantity),
                    fractions=fracs,
                    fee_pct=fee_pct,
                    move_be=bool(settings.paper_move_stop_to_breakeven),
                    expiry_mult_after_tp1=int(settings.paper_expiry_multiplier_after_tp1),
                    early_hours=int(settings.paper_early_scratch_hours),
                    early_mfe_r=float(settings.paper_early_scratch_mfe_r),
                    candles=candles,
                )
                sim.venue = venue_name

                db_exit_fills = [f for f in fills if f.reason != "entry"]
                sim_exit = sim.fills

                # Compare exit reason chain
                db_reasons = [f.reason for f in db_exit_fills]
                sim_reasons = [f.reason for f in sim_exit]
                reasons_match = db_reasons == sim_reasons

                # Compare prices for matching length prefix
                prices_match = True
                for db_f, sim_f in zip(db_exit_fills, sim_exit):
                    if db_f.reason != sim_f.reason or not _rel_close(float(db_f.price), sim_f.price, tol=2e-4):
                        # expiry/early_scratch use candle close — allow wider tol
                        if db_f.reason in (
                            ExitReason.EXPIRED.value,
                            ExitReason.EARLY_SCRATCH.value,
                        ):
                            if db_f.reason == sim_f.reason and _rel_close(
                                float(db_f.price), sim_f.price, tol=5e-3
                            ):
                                continue
                        prices_match = False
                        break
                if len(db_exit_fills) != len(sim_exit):
                    prices_match = False

                status_ok = pos.status == sim.status or (
                    pos.status == "open" and sim.status == "open"
                )
                # Open: sim may still be open; flags should match
                flags_ok = (
                    bool(pos.tp1_filled) == sim.tp1
                    and bool(pos.tp2_filled) == sim.tp2
                    and bool(pos.tp3_filled) == sim.tp3
                )

                ok = reasons_match and prices_match and status_ok and flags_ok
                if ok:
                    seq_ok += 1
                    tag = "OK"
                else:
                    seq_bad += 1
                    tag = "FAIL"
                    detail = (
                        f"{pos.symbol} venue={venue_name} status={pos.status}/{sim.status} "
                        f"exit={pos.exit_reason}/{sim.exit_reason} "
                        f"db_fills={db_reasons} sim_fills={sim_reasons} "
                        f"tp={int(pos.tp1_filled)}{int(pos.tp2_filled)}{int(pos.tp3_filled)}/"
                        f"{int(sim.tp1)}{int(sim.tp2)}{int(sim.tp3)} bars={sim.bars}"
                    )
                    issues.append(detail)
                # Always print one line
                db_px = [f"{f.reason}:{float(f.price):.6g}" for f in db_exit_fills]
                sim_px = [f"{f.reason}:{f.price:.6g}" for f in sim_exit]
                print(
                    f"  {tag:4} {pos.symbol:12} {venue_name:18} "
                    f"{pos.status:6} rpnl={float(pos.realized_pnl or 0):+.2f} "
                    f"db={db_px} sim={sim_px}"
                )

                # Extra: stop fill bar must touch stop on perp
                for f in db_exit_fills:
                    if f.reason != ExitReason.STOP_LOSS.value:
                        continue
                    stop_px = float(f.price)
                    when = ensure_utc(f.filled_at)
                    # find candle at/after fill time within 2h
                    touched = False
                    for c in candles:
                        ot = ensure_utc(c.open_time)
                        if abs((ot - when).total_seconds()) > 3600 * 2:
                            continue
                        if is_long and float(c.low) <= stop_px + 1e-12:
                            touched = True
                            break
                        if (not is_long) and float(c.high) >= stop_px - 1e-12:
                            touched = True
                            break
                    if not touched:
                        msg = f"STOP_NOT_TOUCHED {pos.symbol} stop={stop_px} at={when.isoformat()}"
                        issues.append(msg)
                        print("  FAIL", msg)

            print("=== SUMMARY ===")
            print(f"venue_ok={venue_ok}/{len(open_pos)+len(closed_pos)}")
            print(f"fill_price_level_ok={price_ok} bad={price_bad}")
            print(f"replay_sequence_ok={seq_ok} bad={seq_bad}")
            print(f"issues={len(issues)}")
            if issues:
                print("--- ISSUES ---")
                for i in issues:
                    print(" ", i)
            else:
                print("ALL_CHECKS_PASSED")

            # Live marks for open
            if open_pos:
                print("=== OPEN MARKS (perp last) ===")
                for pos in open_pos:
                    try:
                        last = await router.get_price(pos.symbol)
                        is_long = SignalDirection(pos.direction).is_long
                        direction = 1.0 if is_long else -1.0
                        upnl = (last - float(pos.entry_price)) * float(pos.remaining_quantity) * direction
                        stop = float(pos.current_stop)
                        stop_breach = last <= stop if is_long else last >= stop
                        print(
                            f"  {pos.symbol:12} entry={float(pos.entry_price):.6g} "
                            f"mark={last:.6g} stop={stop:.6g} "
                            f"rem={float(pos.remaining_quantity):.4f} upnl={upnl:+.2f} "
                            f"tp={int(pos.tp1_filled)}{int(pos.tp2_filled)}{int(pos.tp3_filled)} "
                            f"stop_breach_on_mark={stop_breach}"
                        )
                        if stop_breach:
                            issues.append(
                                f"OPEN_STOP_BREACH {pos.symbol} mark={last} stop={stop}"
                            )
                    except Exception as exc:
                        print(f"  {pos.symbol:12} mark_failed: {exc}")
                        issues.append(f"MARK {pos.symbol}: {exc}")

            summary = await container.paper_trading.summary(session)
            prices = {}
            for pos in open_pos:
                try:
                    prices[pos.symbol.upper()] = await router.get_price(pos.symbol)
                except Exception:
                    pass
            summary2 = await container.paper_trading.summary(session, prices=prices or None)
            print("=== EQUITY ===")
            print(f"equity_no_mark={summary.equity:.2f}")
            print(f"equity_with_perp_marks={summary2.equity:.2f}")
            print(f"realized={summary2.realized_pnl:.2f}")
            print(f"open_margin={summary2.open_margin:.2f}")
            print(f"FINAL_OK={len(issues)==0}")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
