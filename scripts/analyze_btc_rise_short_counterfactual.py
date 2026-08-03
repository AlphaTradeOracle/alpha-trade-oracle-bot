"""Counterfactual: BTC rising-momentum short gate vs paper shorts + bounce window.

Usage:
  PYTHONPATH=. python scripts/analyze_btc_rise_short_counterfactual.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database.session import session_scope
from app.market_data.types import Candle
from app.signals.btc_momentum import (
    BtcRiseThresholds,
    btc_rising_short_block_reason,
    compute_btc_rise_metrics,
)

OUT_JSON = ROOT / "exports" / "btc_rise_short_counterfactual.json"
OUT_TXT = ROOT / "exports" / "btc_rise_short_counterfactual.txt"
BOUNCE_START = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
BOUNCE_END = datetime(2026, 8, 3, 16, 0, tzinfo=UTC)


@dataclass
class TradeRow:
    id: int
    symbol: str
    direction: str
    status: str
    opened_at: datetime
    pnl: float
    exit_reason: str | None
    blocked: bool
    detail: str


def _to_candles(rows: list) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        ot = r[0]
        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=UTC)
        o, h, l, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
        closed = bool(r[5]) if len(r) > 5 and r[5] is not None else True
        out.append(
            Candle(
                open_time=ot,
                close_time=ot + timedelta(hours=1),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=0.0,
                is_closed=closed,
            )
        )
    return out


def _slice_closed_before(
    candles: list[Candle], when: datetime, *, timeframe_hours: int
) -> list[Candle]:
    """Closed candles with open_time + tf <= when (no lookahead on open bar)."""
    cutoff = when
    selected: list[Candle] = []
    for c in candles:
        end = c.open_time + timedelta(hours=timeframe_hours)
        if end <= cutoff and c.is_closed:
            selected.append(c)
    return selected


async def main() -> None:
    thresholds = BtcRiseThresholds()
    async with session_scope() as db:
        btc_id = (
            await db.execute(text("SELECT id FROM assets WHERE symbol='BTCUSDT' LIMIT 1"))
        ).scalar_one()

        since = datetime.now(UTC) - timedelta(days=30)
        c1_rows = (
            await db.execute(
                text(
                    """
                    SELECT open_time, open, high, low, close, is_closed
                    FROM market_candles
                    WHERE asset_id=:aid AND timeframe='1h' AND open_time >= :s
                    ORDER BY open_time
                    """
                ),
                {"aid": btc_id, "s": since - timedelta(days=2)},
            )
        ).fetchall()
        c4_rows = (
            await db.execute(
                text(
                    """
                    SELECT open_time, open, high, low, close, is_closed
                    FROM market_candles
                    WHERE asset_id=:aid AND timeframe='4h' AND open_time >= :s
                    ORDER BY open_time
                    """
                ),
                {"aid": btc_id, "s": since - timedelta(days=2)},
            )
        ).fetchall()
        candles_1h_all = _to_candles(list(c1_rows))
        # fix 4h close_time
        candles_4h_all: list[Candle] = []
        for r in c4_rows:
            ot = r[0] if r[0].tzinfo else r[0].replace(tzinfo=UTC)
            candles_4h_all.append(
                Candle(
                    open_time=ot,
                    close_time=ot + timedelta(hours=4),
                    open=float(r[1]),
                    high=float(r[2]),
                    low=float(r[3]),
                    close=float(r[4]),
                    volume=0.0,
                    is_closed=bool(r[5]) if r[5] is not None else True,
                )
            )

        trades_raw = (
            await db.execute(
                text(
                    """
                    SELECT id, symbol, direction, status, opened_at, realized_pnl, exit_reason
                    FROM paper_positions
                    WHERE direction IN ('SHORT', 'STRONG_SHORT')
                      AND opened_at >= :s
                      AND status IN ('closed', 'open')
                      AND COALESCE(exit_reason, '') NOT LIKE 'retest%'
                    ORDER BY opened_at
                    """
                ),
                {"s": since},
            )
        ).fetchall()

        rows: list[TradeRow] = []
        for t in trades_raw:
            opened = t[4]
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=UTC)
            c1 = _slice_closed_before(candles_1h_all, opened, timeframe_hours=1)
            c4 = _slice_closed_before(candles_4h_all, opened, timeframe_hours=4)
            reason = btc_rising_short_block_reason(c1, c4, thresholds=thresholds)
            metrics = compute_btc_rise_metrics(c1, c4)
            rows.append(
                TradeRow(
                    id=int(t[0]),
                    symbol=str(t[1]),
                    direction=str(t[2]),
                    status=str(t[3]),
                    opened_at=opened,
                    pnl=float(t[5] or 0),
                    exit_reason=t[6],
                    blocked=reason is not None,
                    detail=metrics.detail if reason is None else reason,
                )
            )

        # Bounce-window signals
        sig_rows = (
            await db.execute(
                text(
                    """
                    SELECT s.id, a.symbol, s.direction, s.score, s.created_at
                    FROM signals s
                    JOIN assets a ON a.id = s.asset_id
                    WHERE s.created_at >= :a AND s.created_at < :b
                      AND s.direction IN ('SHORT', 'STRONG_SHORT')
                    ORDER BY s.created_at
                    """
                ),
                {"a": BOUNCE_START, "b": BOUNCE_END},
            )
        ).fetchall()

        bounce_blocked = 0
        bounce_total = 0
        bounce_samples: list[dict] = []
        for s in sig_rows:
            bounce_total += 1
            created = s[4]
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            c1 = _slice_closed_before(candles_1h_all, created, timeframe_hours=1)
            c4 = _slice_closed_before(candles_4h_all, created, timeframe_hours=4)
            reason = btc_rising_short_block_reason(c1, c4, thresholds=thresholds)
            if reason:
                bounce_blocked += 1
            if len(bounce_samples) < 15:
                bounce_samples.append(
                    {
                        "id": s[0],
                        "symbol": s[1],
                        "direction": s[2],
                        "score": float(s[3] or 0),
                        "created_at": created.isoformat(),
                        "blocked": reason is not None,
                        "reason": reason,
                    }
                )

    before_n = len(rows)
    before_pnl = sum(r.pnl for r in rows)
    before_wins = sum(1 for r in rows if r.pnl > 0)
    kept = [r for r in rows if not r.blocked]
    removed = [r for r in rows if r.blocked]
    after_n = len(kept)
    after_pnl = sum(r.pnl for r in kept)
    after_wins = sum(1 for r in kept if r.pnl > 0)
    removed_pnl = sum(r.pnl for r in removed)

    summary = {
        "window_days": 30,
        "thresholds": asdict(thresholds),
        "paper_shorts": {
            "before": {
                "n": before_n,
                "pnl": round(before_pnl, 2),
                "winrate": round(before_wins / before_n, 4) if before_n else None,
            },
            "after_gate": {
                "n": after_n,
                "pnl": round(after_pnl, 2),
                "winrate": round(after_wins / after_n, 4) if after_n else None,
            },
            "blocked": {
                "n": len(removed),
                "pnl_removed": round(removed_pnl, 2),
                "delta_pnl": round(after_pnl - before_pnl, 2),
            },
            "blocked_trades": [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "opened_at": r.opened_at.isoformat(),
                    "pnl": round(r.pnl, 2),
                    "detail": r.detail,
                }
                for r in removed
            ],
        },
        "bounce_window_signals": {
            "start": BOUNCE_START.isoformat(),
            "end": BOUNCE_END.isoformat(),
            "total_short": bounce_total,
            "would_block": bounce_blocked,
            "block_rate": round(bounce_blocked / bounce_total, 4) if bounce_total else None,
            "samples": bounce_samples,
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "BTC rising short-gate counterfactual",
        f"Paper shorts ~30d: before n={before_n} pnl={before_pnl:.2f} "
        f"wr={before_wins / before_n if before_n else 0:.1%}",
        f"After gate:          n={after_n} pnl={after_pnl:.2f} "
        f"wr={after_wins / after_n if after_n else 0:.1%}",
        f"Blocked:             n={len(removed)} removed_pnl={removed_pnl:.2f} "
        f"delta_pnl={after_pnl - before_pnl:+.2f}",
        f"Bounce {BOUNCE_START.isoformat()}–{BOUNCE_END.isoformat()}: "
        f"{bounce_blocked}/{bounce_total} SHORT signals would be blocked",
    ]
    for r in removed[:20]:
        lines.append(
            f"  blocked #{r.id} {r.symbol} @ {r.opened_at.isoformat()} pnl={r.pnl:.2f}"
        )
    text_out = "\n".join(lines) + "\n"
    OUT_TXT.write_text(text_out, encoding="utf-8")
    print(text_out)
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    asyncio.run(main())
