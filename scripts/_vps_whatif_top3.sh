#!/usr/bin/env bash
# What-if replay for top near-miss longs (PIEVERSE, ZEST, G).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
\pset border 2

\echo === signal specs ===
SELECT a.symbol,
       s.id AS signal_id,
       s.direction,
       ROUND(s.score::numeric,2) AS score,
       ROUND(s.reference_price::numeric, 8) AS ref,
       ROUND(s.entry_low::numeric, 8) AS entry_lo,
       ROUND(s.entry_high::numeric, 8) AS entry_hi,
       ROUND(s.stop_loss::numeric, 8) AS sl,
       ROUND(s.take_profit_1::numeric, 8) AS tp1,
       ROUND(s.take_profit_2::numeric, 8) AS tp2,
       ROUND(s.take_profit_3::numeric, 8) AS tp3,
       ROUND(s.risk_reward_ratio::numeric,2) AS rr,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.id IN (
  SELECT s2.id FROM signals s2
  JOIN assets a2 ON a2.id = s2.asset_id
  WHERE a2.symbol IN ('PIEVERSEUSDT','ZESTUSDT','GUSDT')
    AND s2.created_at >= NOW() - INTERVAL '48 hours'
    AND s2.score >= 81
  ORDER BY s2.score DESC
  LIMIT 3
)
ORDER BY s.score DESC;
SQL

# Python what-if using worker + kucoin/binance candles via app providers
docker compose exec -T worker python - <<'PY'
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.container import build_container
from app.core.config import get_settings
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.models.market import Asset, MarketCandle
from app.models.signal import Signal


SYMBOLS = ["PIEVERSEUSDT", "ZESTUSDT", "GUSDT"]


def mid(lo, hi, ref):
    if lo and hi:
        return (float(lo) + float(hi)) / 2.0
    if lo:
        return float(lo)
    if hi:
        return float(hi)
    return float(ref)


async def load_candles(session, asset_id, start, end, tf="15m"):
    q = await session.execute(
        select(MarketCandle)
        .where(
            MarketCandle.asset_id == asset_id,
            MarketCandle.timeframe == tf,
            MarketCandle.open_time >= start,
            MarketCandle.open_time <= end,
            MarketCandle.is_closed.is_(True),
        )
        .order_by(MarketCandle.open_time)
    )
    return list(q.scalars())


async def fetch_live(provider, symbol, start, end):
    """Fallback: pull 15m from exchange if DB thin."""
    try:
        series = await provider.get_candles(
            symbol, "15m", limit=500, start_time=start, end_time=end
        )
        bars = list(series.candles)
    except Exception as exc:
        print(f"  live_fetch_fail {symbol}: {exc}")
        return []
    out = []
    for b in bars:
        ot = ensure_utc(b.open_time)
        if ot < start or ot > end:
            continue
        out.append(b)
    out.sort(key=lambda x: ensure_utc(x.open_time))
    return out


def simulate_long(entry, sl, tp1, tp2, tp3, bars, scale=(0.4, 0.3, 0.3)):
    """Simple long what-if: entry at signal+next bar open or mid entry; SL/TP on wick."""
    if not bars:
        return {"status": "no_data"}

    # Fill at first bar open after signal (conservative) or entry mid if in range
    filled = False
    fill_px = entry
    fill_i = 0
    for i, b in enumerate(bars):
        o, h, l, c = float(b.open), float(b.high), float(b.low), float(b.close)
        # assume limit entry zone around entry: fill if low<=entry<=high or open crosses
        if l <= entry <= h or o <= entry:
            filled = True
            fill_px = entry
            fill_i = i
            break
    if not filled:
        # market next open
        fill_px = float(bars[0].open)
        fill_i = 0
        filled = True

    risk = abs(fill_px - sl)
    if risk <= 0:
        return {"status": "bad_levels", "entry": fill_px}

    remaining = 1.0
    realized_r = 0.0
    path = []
    be = False
    stop = sl
    tps = [(tp1, scale[0]), (tp2, scale[1]), (tp3, scale[2])]
    hit = [False, False, False]
    mfe_r = 0.0
    mae_r = 0.0
    exit_reason = None
    exit_px = None
    exit_time = None

    for b in bars[fill_i:]:
        o, h, l, c = float(b.open), float(b.high), float(b.low), float(b.close)
        t = ensure_utc(b.open_time).isoformat()
        # MFE/MAE from fill
        mfe_r = max(mfe_r, (h - fill_px) / risk)
        mae_r = min(mae_r, (l - fill_px) / risk)

        # stop first (conservative: if both stop and TP in same bar, stop wins)
        if l <= stop:
            realized_r += remaining * ((stop - fill_px) / risk)
            remaining = 0.0
            exit_reason = "stop" if not be else "be_stop"
            exit_px = stop
            exit_time = t
            break

        for idx, (tp, frac) in enumerate(tps):
            if hit[idx] or tp is None:
                continue
            if h >= tp:
                hit[idx] = True
                slice_r = frac * ((tp - fill_px) / risk)
                realized_r += slice_r
                remaining -= frac
                path.append(f"TP{idx+1}@{tp:.6g} (+{slice_r:.2f}R)")
                if idx == 0:
                    # move to BE after TP1
                    be = True
                    stop = fill_px
                if remaining <= 1e-9:
                    remaining = 0.0
                    exit_reason = "tps_full"
                    exit_px = tp
                    exit_time = t
                    break
        if remaining <= 1e-9:
            break

    if remaining > 0:
        last = bars[-1]
        mark = float(last.close)
        unreal_r = remaining * ((mark - fill_px) / risk)
        exit_reason = exit_reason or "still_open"
        exit_px = mark
        exit_time = ensure_utc(last.open_time).isoformat()
        total_r = realized_r + unreal_r
    else:
        unreal_r = 0.0
        total_r = realized_r

    # $300 margin, 1R = $300
    pnl_usd = total_r * 300.0
    return {
        "status": exit_reason,
        "entry": round(fill_px, 8),
        "stop_final": round(stop, 8),
        "realized_r": round(realized_r, 3),
        "unreal_r": round(unreal_r, 3),
        "total_r": round(total_r, 3),
        "pnl_usd": round(pnl_usd, 2),
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "path": path,
        "exit_px": exit_px,
        "exit_time": exit_time,
        "bars": len(bars) - fill_i,
        "tps_hit": sum(1 for x in hit if x),
    }


async def main():
    settings = get_settings()
    container = build_container(settings)
    provider = container.provider

    async with session_scope() as session:
        rows = []
        for sym in SYMBOLS:
            q = await session.execute(
                select(Signal, Asset)
                .join(Asset, Asset.id == Signal.asset_id)
                .where(Asset.symbol == sym, Signal.created_at >= datetime.now(timezone.utc) - timedelta(hours=48))
                .order_by(desc(Signal.score))
                .limit(1)
            )
            row = q.first()
            if not row:
                print(f"{sym}: no signal")
                continue
            sig, asset = row
            rows.append((sig, asset))

        print("=== WHAT-IF LONG (scale 40/30/30, BE after TP1, $300=1R) ===\n")
        for sig, asset in rows:
            entry = mid(sig.entry_low, sig.entry_high, sig.reference_price)
            sl = float(sig.stop_loss) if sig.stop_loss else None
            tp1 = float(sig.take_profit_1) if sig.take_profit_1 else None
            tp2 = float(sig.take_profit_2) if sig.take_profit_2 else None
            tp3 = float(sig.take_profit_3) if sig.take_profit_3 else None
            created = ensure_utc(sig.created_at)
            end = datetime.now(timezone.utc)

            bars = await load_candles(session, asset.id, created - timedelta(minutes=15), end, "15m")
            source = "db"
            if len(bars) < 3:
                live = await fetch_live(provider, asset.symbol, created, end)
                if live:
                    bars = live
                    source = "live"

            # filter bars at/after signal
            bars = [b for b in bars if ensure_utc(getattr(b, "open_time")) >= created - timedelta(minutes=1)]

            print(f"{asset.symbol}  score={float(sig.score):.2f}  {created.isoformat()}  dir={sig.direction}")
            print(
                f"  levels entry={entry:.8g} sl={sl} tp1={tp1} tp2={tp2} tp3={tp3}  bars={len(bars)} ({source})"
            )
            if sl is None or tp1 is None:
                print("  SKIP: missing SL/TP")
                print()
                continue
            sim = simulate_long(entry, sl, tp1, tp2, tp3, bars)
            print(
                f"  RESULT {sim.get('status')}  total={sim.get('total_r')}R  "
                f"(${sim.get('pnl_usd')})  realized={sim.get('realized_r')}R  "
                f"unreal={sim.get('unreal_r')}R"
            )
            print(
                f"  MFE={sim.get('mfe_r')}R  MAE={sim.get('mae_r')}R  "
                f"TPs={sim.get('tps_hit')}  path={sim.get('path')}"
            )
            print(f"  exit={sim.get('exit_px')} @ {sim.get('exit_time')}")
            print()

    await container.aclose()


asyncio.run(main())
PY
