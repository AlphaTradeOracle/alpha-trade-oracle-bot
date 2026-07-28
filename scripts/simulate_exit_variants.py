"""Exit-Varianten auf bestehenden Paper-Entries simulieren (OHLC-Replay).

Varianten:
  fixed_tight   — TP 1.5/2.5/4.0R, Scale-out, BE nach TP1
  fixed_wide    — TP 2.0/4.0/6.0R, Scale-out, BE nach TP1
  fixed_prev    — TP 2.0/3.5/5.5R (vorherige Wide-Defaults)
  trail_after_tp2 — Wide TP1+TP2, danach ATR-Trail auf Rest
  trail_after_tp1 — Wide TP1, danach ATR-Trail auf Rest
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import utc_now
from app.database.session import session_scope
from app.repositories.paper_repository import PaperRepository
from app.signals.risk import RiskManager

FEE = 0.001  # 0.1%
SCALE = (Decimal("0.33333333"), Decimal("0.33333333"), Decimal("0.33333334"))
TRAIL_ATR_MULT = 1.5


@dataclass
class SimPos:
    symbol: str
    direction: str
    entry: float
    stop: float
    qty: float
    margin: float
    notional: float
    opened_at: datetime
    timeframe: str = "1h"


@dataclass
class SimResult:
    mode: str
    symbol: str
    pnl: float
    fees: float
    exit_reason: str
    bars: int = 0
    tp1: bool = False
    tp2: bool = False
    tp3: bool = False
    trailed: bool = False


def _tps(entry: float, stop: float, is_long: bool, mults: tuple[float, float, float]) -> tuple[float, float, float]:
    return RiskManager.targets_from_stop(entry, stop, is_long=is_long, multipliers=mults)


def _atr(candles: list, idx: int, period: int = 14) -> float:
    if idx < 1:
        return abs(float(candles[idx].high) - float(candles[idx].low))
    start = max(1, idx - period + 1)
    trs: list[float] = []
    for i in range(start, idx + 1):
        h = float(candles[i].high)
        l = float(candles[i].low)
        prev_c = float(candles[i - 1].close)
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return sum(trs) / len(trs) if trs else abs(float(candles[idx].high) - float(candles[idx].low))


def _simulate(
    pos: SimPos,
    candles: list,
    *,
    mode: str,
    tp_mults: tuple[float, float, float],
    trail_after: int | None,
) -> SimResult:
    is_long = "LONG" in pos.direction
    entry = pos.entry
    stop = pos.stop
    tp1, tp2, tp3 = _tps(entry, stop, is_long, tp_mults)
    qty0 = Decimal(str(pos.qty))
    rem = qty0
    realized = Decimal("0")
    fees = Decimal(str(pos.notional)) * Decimal(str(FEE))  # entry fee on notional
    realized -= fees  # account entry fee like paper
    current_stop = stop
    tp1_hit = tp2_hit = tp3_hit = False
    trail_on = False
    extreme = entry
    exit_reason = "open"
    bars = 0

    def reduce(price: float, fraction: Decimal | None, reason: str, all_rest: bool = False) -> None:
        nonlocal rem, realized, fees, exit_reason
        if rem <= 0:
            return
        if all_rest or fraction is None:
            qty = rem
        else:
            qty = min(qty0 * fraction, rem)
        if qty <= 0:
            return
        direction = Decimal("1") if is_long else Decimal("-1")
        px = Decimal(str(price))
        gross = (px - Decimal(str(entry))) * qty * direction
        fee = px * qty * Decimal(str(FEE))
        net = gross - fee
        rem -= qty
        realized += net
        fees += fee
        exit_reason = reason
        if rem <= Decimal("0.00000001"):
            rem = Decimal("0")

    for i, c in enumerate(candles):
        if rem <= 0:
            break
        # only bars at/after open
        if c.open_time < pos.opened_at:
            continue
        bars += 1
        high = float(c.high)
        low = float(c.low)
        close = float(c.close)

        # Stop check first
        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            reduce(current_stop, None, "stop_loss" if current_stop != entry else "break_even", all_rest=True)
            break

        # Fixed TPs / trail activation
        if not trail_on:
            if not tp1_hit:
                hit = high >= tp1 if is_long else low <= tp1
                if hit:
                    reduce(tp1, SCALE[0], "take_profit_1")
                    tp1_hit = True
                    current_stop = entry  # BE
                    if trail_after == 1:
                        trail_on = True
                        extreme = high if is_long else low
                        continue
            if tp1_hit and not tp2_hit and trail_after != 1:
                hit = high >= tp2 if is_long else low <= tp2
                if hit:
                    reduce(tp2, SCALE[1], "take_profit_2")
                    tp2_hit = True
                    if trail_after == 2:
                        trail_on = True
                        extreme = high if is_long else low
                        continue
                    # fixed mode continues to TP3
            if tp2_hit and not tp3_hit and trail_after is None:
                hit = high >= tp3 if is_long else low <= tp3
                if hit:
                    reduce(tp3, None, "take_profit_3", all_rest=True)
                    tp3_hit = True
                    break

        if trail_on and rem > 0:
            if is_long:
                extreme = max(extreme, high)
                atr = _atr(candles, i) * TRAIL_ATR_MULT
                trail_stop = extreme - atr
                # never loosen below BE once trailing
                trail_stop = max(trail_stop, entry)
                if low <= trail_stop:
                    reduce(trail_stop, None, "trailing_stop", all_rest=True)
                    break
                current_stop = max(current_stop, trail_stop)
            else:
                extreme = min(extreme, low)
                atr = _atr(candles, i) * TRAIL_ATR_MULT
                trail_stop = extreme + atr
                trail_stop = min(trail_stop, entry)
                if high >= trail_stop:
                    reduce(trail_stop, None, "trailing_stop", all_rest=True)
                    break
                current_stop = min(current_stop, trail_stop)

    if rem > 0:
        # mark at last close
        last = float(candles[-1].close) if candles else entry
        direction = Decimal("1") if is_long else Decimal("-1")
        qty = rem
        px = Decimal(str(last))
        gross = (px - Decimal(str(entry))) * qty * direction
        fee = px * qty * Decimal(str(FEE))
        realized += gross - fee
        fees += fee
        rem = Decimal("0")
        exit_reason = "mark_to_market"

    return SimResult(
        mode=mode,
        symbol=pos.symbol,
        pnl=float(realized),
        fees=float(fees),
        exit_reason=exit_reason,
        bars=bars,
        tp1=tp1_hit,
        tp2=tp2_hit,
        tp3=tp3_hit,
        trailed=trail_on and exit_reason == "trailing_stop",
    )


MODES = {
    "fixed_tight": {"tp": (1.5, 2.5, 4.0), "trail_after": None},
    "fixed_wide": {"tp": (2.0, 4.0, 6.0), "trail_after": None},
    "fixed_prev": {"tp": (2.0, 3.5, 5.5), "trail_after": None},
    "trail_after_tp2": {"tp": (2.0, 4.0, 6.0), "trail_after": 2},
    "trail_after_tp1": {"tp": (2.0, 4.0, 6.0), "trail_after": 1},
}


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    async with session_scope() as session:
        account = await container.paper_trading.get_or_create_account(session)
        positions = await PaperRepository(session).list_positions(account.id)

    if not positions:
        print("No paper positions", file=sys.stderr)
        await container.aclose()
        return 1

    entries: list[SimPos] = []
    for p in positions:
        entries.append(
            SimPos(
                symbol=p.symbol,
                direction=p.direction,
                entry=float(p.entry_price),
                stop=float(p.stop_loss),
                qty=float(p.initial_quantity),
                margin=float(p.margin_used or 100),
                notional=float(p.notional),
                opened_at=p.opened_at,
                timeframe=p.timeframe or "1h",
            )
        )

    print(f"Simulating {len(entries)} paper entries × {len(MODES)} modes ...", file=sys.stderr)

    candle_cache: dict[str, list] = {}
    results: list[SimResult] = []
    try:
        for e in entries:
            if e.symbol not in candle_cache:
                try:
                    series = await container.provider.get_candles(
                        e.symbol,
                        e.timeframe,
                        limit=100_000,
                        start_time=e.opened_at,
                        end_time=utc_now(),
                    )
                    candle_cache[e.symbol] = list(series.candles)
                    print(f"  candles {e.symbol}: {len(candle_cache[e.symbol])}", file=sys.stderr)
                except Exception as exc:
                    print(f"  SKIP {e.symbol}: {exc}", file=sys.stderr)
                    candle_cache[e.symbol] = []
            candles = candle_cache[e.symbol]
            if not candles:
                continue
            for mode, cfg in MODES.items():
                results.append(
                    _simulate(
                        e,
                        candles,
                        mode=mode,
                        tp_mults=cfg["tp"],  # type: ignore[arg-type]
                        trail_after=cfg["trail_after"],  # type: ignore[arg-type]
                    )
                )
    finally:
        await container.aclose()

    by_mode: dict[str, list[SimResult]] = {m: [] for m in MODES}
    for r in results:
        by_mode[r.mode].append(r)

    summary = []
    for mode, rows in by_mode.items():
        total = sum(r.pnl for r in rows)
        summary.append(
            {
                "mode": mode,
                "tp": list(MODES[mode]["tp"]),  # type: ignore[arg-type]
                "trail_after_tp": MODES[mode]["trail_after"],
                "trail_atr_mult": TRAIL_ATR_MULT,
                "symbols": len(rows),
                "total_pnl": round(total, 2),
                "wins": sum(1 for r in rows if r.pnl > 0),
                "losses": sum(1 for r in rows if r.pnl < 0),
                "per_symbol": [
                    {
                        "symbol": r.symbol,
                        "pnl": round(r.pnl, 2),
                        "exit": r.exit_reason,
                        "tp1": r.tp1,
                        "tp2": r.tp2,
                        "tp3": r.tp3,
                        "trailed": r.trailed,
                    }
                    for r in sorted(rows, key=lambda x: x.pnl, reverse=True)
                ],
            }
        )

    summary.sort(key=lambda x: x["total_pnl"], reverse=True)
    payload = {
        "generated_at": utc_now().isoformat(),
        "fee_percent": 0.1,
        "scale_out": [float(x) for x in SCALE],
        "winner": summary[0]["mode"] if summary else None,
        "ranking": summary,
    }
    print(json.dumps(payload, indent=2))
    print(
        f"WINNER: {payload['winner']}  "
        + " | ".join(f"{s['mode']}={s['total_pnl']:+.2f}" for s in summary),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
