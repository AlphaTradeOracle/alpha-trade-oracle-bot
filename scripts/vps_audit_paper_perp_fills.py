"""Sample-audit: would closed paper TP/SL still hit on perpetual 5m OHLC?

Run inside app container:
  python /tmp/_vps_audit_paper_perp_fills.py --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from app.core.enums import ExitReason, SignalDirection
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.market_data.perp_router import PerpRouterProvider
from app.models.paper import PaperPosition

logger = get_logger(__name__)

TP_REASONS = {
    ExitReason.TAKE_PROFIT_1.value,
    ExitReason.TAKE_PROFIT_2.value,
    ExitReason.TAKE_PROFIT_3.value,
}
STOP_REASONS = {ExitReason.STOP_LOSS.value}
INTERESTING = TP_REASONS | STOP_REASONS | {ExitReason.EXPIRED.value}


@dataclass
class RowResult:
    symbol: str
    direction: str
    exit_reason: str
    venue: str | None
    recorded_ok: bool | None
    detail: str
    realized: float


def _level_for_reason(pos: PaperPosition, reason: str) -> float | None:
    if reason == ExitReason.TAKE_PROFIT_1.value:
        return float(pos.take_profit_1)
    if reason == ExitReason.TAKE_PROFIT_2.value:
        return float(pos.take_profit_2)
    if reason == ExitReason.TAKE_PROFIT_3.value:
        return float(pos.take_profit_3)
    if reason == ExitReason.STOP_LOSS.value:
        # Trailing may have moved the live stop away from the initial SL.
        return float(pos.current_stop or pos.stop_loss)
    return None


def _touched(
    *,
    is_long: bool,
    reason: str,
    level: float,
    highs: list[float],
    lows: list[float],
) -> bool:
    if not highs:
        return False
    hi = max(highs)
    lo = min(lows)
    if reason in TP_REASONS:
        return hi >= level if is_long else lo <= level
    if reason in STOP_REASONS:
        return lo <= level if is_long else hi >= level
    return False


async def audit_one(router: PerpRouterProvider, pos: PaperPosition) -> RowResult:
    reason = pos.exit_reason or ""
    is_long = SignalDirection(pos.direction).is_long
    opened = ensure_utc(pos.opened_at) if pos.opened_at else None
    closed = ensure_utc(pos.closed_at) if pos.closed_at else None
    realized = float(pos.realized_pnl or 0)

    if opened is None or closed is None:
        return RowResult(
            pos.symbol, pos.direction, reason, None, None, "missing_timestamps", realized
        )

    try:
        venue = await router.resolve_venue(pos.symbol)
        venue_name = venue.name
    except Exception as exc:
        return RowResult(
            pos.symbol, pos.direction, reason, None, None, f"unroutable:{exc}", realized
        )

    # Pad window so first/last bar is included.
    start = opened - timedelta(minutes=5)
    end = closed + timedelta(minutes=10)
    span_min = max(1.0, (end - start).total_seconds() / 60.0)
    limit = min(1500, int(span_min / 5) + 5)

    try:
        series = await router.get_candles(
            pos.symbol,
            "5m",
            limit=limit,
            start_time=start,
            end_time=end,
            include_unclosed=True,
        )
    except Exception as exc:
        return RowResult(
            pos.symbol,
            pos.direction,
            reason,
            venue_name,
            None,
            f"candles_failed:{exc}",
            realized,
        )

    candles = [
        c
        for c in series.candles
        if ensure_utc(c.open_time) <= closed + timedelta(minutes=5)
        and ensure_utc(c.close_time) >= opened - timedelta(minutes=5)
    ]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    if reason == ExitReason.EXPIRED.value:
        # Expiry uses last/mark — only flag if window empty.
        ok = bool(candles)
        detail = f"expiry_bars={len(candles)}"
        return RowResult(pos.symbol, pos.direction, reason, venue_name, ok, detail, realized)

    level = _level_for_reason(pos, reason)
    if level is None:
        return RowResult(
            pos.symbol, pos.direction, reason, venue_name, None, "no_level", realized
        )

    hit = _touched(is_long=is_long, reason=reason, level=level, highs=highs, lows=lows)
    if hit:
        detail = f"HIT level={level:.6g} bars={len(candles)} hi={max(highs):.6g} lo={min(lows):.6g}"
    else:
        detail = (
            f"MISS level={level:.6g} bars={len(candles)} "
            f"hi={max(highs) if highs else float('nan'):.6g} "
            f"lo={min(lows) if lows else float('nan'):.6g}"
        )
    return RowResult(pos.symbol, pos.direction, reason, venue_name, hit, detail, realized)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    configure_logging("INFO", json_output=False)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(PaperPosition)
                .where(PaperPosition.status == "closed")
                .where(PaperPosition.exit_reason.in_(tuple(INTERESTING)))
                .where(PaperPosition.closed_at.is_not(None))
                .order_by(PaperPosition.closed_at.desc())
                .limit(args.limit)
            )
        ).scalars().all()

    print(f"sampled_closed={len(rows)} limit={args.limit}")
    router = PerpRouterProvider()
    try:
        results: list[RowResult] = []
        for pos in rows:
            results.append(await audit_one(router, pos))
    finally:
        await router.close()

    checked = [r for r in results if r.recorded_ok is not None and r.exit_reason in (TP_REASONS | STOP_REASONS)]
    hits = [r for r in checked if r.recorded_ok]
    misses = [r for r in checked if r.recorded_ok is False]
    unknown = [r for r in results if r.recorded_ok is None]

    print("--- summary ---")
    print(f"tp_sl_checked={len(checked)}")
    print(f"perp_would_hit={len(hits)}")
    print(f"perp_would_MISS={len(misses)}")
    if checked:
        print(f"miss_rate_pct={100 * len(misses) / len(checked):.1f}")
    print(f"unknown_or_expired={len(unknown)}")
    miss_pnl = sum(r.realized for r in misses)
    hit_pnl = sum(r.realized for r in hits)
    print(f"realized_pnl_on_MISSes={miss_pnl:.2f}")
    print(f"realized_pnl_on_HITs={hit_pnl:.2f}")

    print("--- MISSES (spot close likely invalid on perp) ---")
    for r in misses[:30]:
        print(
            f"{r.symbol:12} {r.direction:12} {r.exit_reason:16} "
            f"venue={r.venue} pnl={r.realized:+.2f} | {r.detail}"
        )

    print("--- sample HITs ---")
    for r in hits[:10]:
        print(
            f"{r.symbol:12} {r.direction:12} {r.exit_reason:16} "
            f"venue={r.venue} pnl={r.realized:+.2f} | {r.detail}"
        )

    if unknown:
        print("--- unknown / skipped ---")
        for r in unknown[:15]:
            print(
                f"{r.symbol:12} {r.exit_reason:16} venue={r.venue} | {r.detail}"
            )


if __name__ == "__main__":
    asyncio.run(main())
