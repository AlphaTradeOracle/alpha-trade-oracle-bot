"""Simulate equity levers since paper reset → what gets us toward ~$8–10k.

Rebuilds a fill book from stored signals under Combo geometry (ATR×1.8,
zone 0.40–1.15, $300×10), then sweeps book / selection policies.

  python scripts/simulate_equity_levers.py --out /tmp/equity_levers.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, timeframe_to_timedelta, utc_now
from app.database.session import session_scope
from app.market_data.types import Candle
from app.models.paper import PaperAccount
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.signals.retest_entry import (
    RetestEntryConfig,
    arm_retest_entry,
    levels_from_entry_sl,
    wilder_atr,
)
from app.signals.risk import RiskManager

import importlib.util

_combo_path = ROOT / "scripts" / "compare_paper_equity_combo.py"
_spec = importlib.util.spec_from_file_location("compare_paper_equity_combo", _combo_path)
assert _spec and _spec.loader
_combo = importlib.util.module_from_spec(_spec)
sys.modules["compare_paper_equity_combo"] = _combo
_spec.loader.exec_module(_combo)
_candles_from_df = _combo._candles_from_df
_curve = _combo._curve
_daily = _combo._daily
_idx_at = _combo._idx_at
_replay = _combo._replay
_size = _combo._size

SL_ATR = 1.8
ZONE_NEAR = 0.40
ZONE_FAR = 1.15
SINCE = datetime.fromisoformat("2026-07-31T16:32:35+00:00")
TP_MULTS = (1.5, 2.5, 4.0)


def _long(direction: str) -> bool:
    return SignalDirection(direction).is_long


def _apply_book(
    trades: list[dict[str, Any]],
    *,
    start_equity: float,
    start_at: datetime,
    end_at: datetime,
    max_open: int,
    max_per_direction: int,
    prefer: str | None = None,
) -> dict[str, Any]:
    """Chronological book.

    prefer:
      - ``pnl``: hindsight — best realized PnL first (oracle ceiling)
      - ``score``: liveable — extreme scores first (short low / long high)
    """

    def _rank(t: dict[str, Any]) -> tuple:
        if prefer == "pnl":
            return (-float(t["net_pnl"]),)
        if prefer == "score":
            # More extreme conviction first.
            sc = float(t.get("score") or 50.0)
            return (-sc if t["side"] == "LONG" else sc,)
        return (0.0,)

    ordered = sorted(
        trades,
        key=lambda t: (t["entry_at"], *_rank(t), t["symbol"], t["id"]),
    )
    open_book: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    skipped = 0
    skip_max = skip_dir = 0
    for trade in ordered:
        entry = trade["entry_at"]
        open_book = [o for o in open_book if o["exit_at"] > entry]
        dir_count = sum(1 for o in open_book if o["side"] == trade["side"])
        if len(open_book) >= max_open:
            skipped += 1
            skip_max += 1
            continue
        if dir_count >= max_per_direction:
            skipped += 1
            skip_dir += 1
            continue
        open_book.append(trade)
        accepted.append(trade)

    events = [(t["exit_at"], float(t["net_pnl"])) for t in accepted]
    end_eq = start_equity + sum(p for _, p in events)
    wins = sum(1 for t in accepted if float(t["net_pnl"]) > 0)
    curve = _curve(events, start=start_equity, start_at=start_at, end_eq=end_eq, end_at=end_at)
    return {
        "accepted_n": len(accepted),
        "skipped_n": skipped,
        "skip_max_open": skip_max,
        "skip_max_per_dir": skip_dir,
        "wins": wins,
        "win_rate": round(wins / len(accepted), 3) if accepted else 0.0,
        "net_pnl": round(sum(float(t["net_pnl"]) for t in accepted), 2),
        "end_equity": round(end_eq, 2),
        "return_pct": round((end_eq / start_equity - 1) * 100, 2) if start_equity else 0.0,
        "peak_open": _peak_open(accepted),
        "daily": _daily(curve),
    }


def _peak_open(trades: list[dict[str, Any]]) -> int:
    events: list[tuple[datetime, int]] = []
    for t in trades:
        events.append((t["entry_at"], 1))
        events.append((t["exit_at"], -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = mx = 0
    for _, d in events:
        cur += d
        mx = max(mx, cur)
    return mx


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/equity_levers.json")
    parser.add_argument("--since", default=SINCE.isoformat())
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    fee = float(settings.paper_fee_percent)
    move_be = bool(settings.paper_move_stop_to_breakeven)
    risk_usd = float(settings.paper_risk_per_trade_usd)
    leverage = float(settings.paper_leverage)
    max_notional = float(settings.paper_max_notional_usd)
    margin = float(settings.paper_margin_per_trade)
    pending_mult = int(settings.paper_retest_pending_multiplier)
    since = ensure_utc(datetime.fromisoformat(args.since))
    now = utc_now()

    async with session_scope() as session:
        acct = (
            await session.execute(select(PaperAccount).where(PaperAccount.name == "default"))
        ).scalar_one()
        initial = float(acct.initial_balance)
        live_equity = float(acct.cash_balance) + 0.0  # open margin ~0 after rebuild
        signals = (
            await session.execute(
                select(Signal)
                .where(
                    Signal.created_at >= since,
                    Signal.direction.in_(
                        ("LONG", "SHORT", "STRONG_LONG", "STRONG_SHORT")
                    ),
                )
                .order_by(Signal.created_at.asc())
                .limit(8000)
            )
        ).scalars().all()
        asset_ids = list({s.asset_id for s in signals})
        symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)

    # Gate like paper
    gated: list[tuple[Signal, str]] = []
    for s in signals:
        try:
            d = SignalDirection(s.direction)
        except ValueError:
            continue
        if not d.is_actionable:
            continue
        score = float(s.score)
        if d.is_long and score < float(settings.signal_min_score):
            continue
        if d.is_short and score > float(settings.signal_short_max_score):
            continue
        if d.is_short and score <= float(settings.signal_short_min_score):
            continue
        if s.stop_loss is None or s.reference_price is None:
            continue
        sym = symbols_by_id.get(s.asset_id)
        if not sym:
            continue
        gated.append((s, sym.upper()))

    print(f"signals_gated={len(gated)} initial={initial}", file=sys.stderr, flush=True)

    needed: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for s, sym in gated:
        tf = s.primary_timeframe or "1h"
        start = ensure_utc(s.created_at) - timeframe_to_timedelta(tf) * 40
        key = (sym, tf)
        if key not in needed:
            needed[key] = (start, now)
        else:
            a, b = needed[key]
            needed[key] = (min(a, start), max(b, now))

    cache: dict[tuple[str, str], list[Candle]] = {}
    print(f"Loading {len(needed)} candle series...", file=sys.stderr, flush=True)
    async with session_scope() as session:
        repo = AssetRepository(session)
        for i, ((sym, tf), (start, end)) in enumerate(needed.items(), start=1):
            series = await repo.load_candle_series(
                sym, tf, start_time=start, end_time=end, limit=100_000
            )
            if series is None or series.is_empty:
                cache[(sym, tf)] = []
            else:
                df = series.to_dataframe()
                if "open_time" in df.columns:
                    import pandas as pd

                    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
                    df = df.set_index("open_time", drop=False)
                cache[(sym, tf)] = _candles_from_df(df, tf)
            if i % 40 == 0 or i == len(needed):
                print(f"  {i}/{len(needed)}", file=sys.stderr, flush=True)

    cfg = RetestEntryConfig(
        zone_near=Decimal(str(ZONE_NEAR)),
        zone_far=Decimal(str(ZONE_FAR)),
        pending_multiplier=pending_mult,
        min_bars_in_zone=1,
        trendline_gate_enabled=bool(settings.signal_trendline_gate_enabled),
        trendline_buffer_atr=float(settings.signal_trendline_buffer_atr),
        trendline_lookback=int(settings.signal_trendline_lookback),
        trendline_min_points=int(settings.signal_trendline_min_points),
        trendline_min_r2=float(settings.signal_trendline_min_r2),
        trendline_min_clearance_atr=float(settings.signal_trendline_min_clearance_atr),
    )

    candidates: list[dict[str, Any]] = []
    stats = {"armed": 0, "filled": 0, "no_atr": 0, "no_fill": 0, "busy_symbol": 0}
    # one position per symbol at a time (busy until exit)
    symbol_free_at: dict[str, datetime] = {}

    for s, sym in gated:
        tf = s.primary_timeframe or "1h"
        cs = cache.get((sym, tf)) or []
        if not cs:
            continue
        armed = ensure_utc(s.created_at)
        if sym in symbol_free_at and armed < symbol_free_at[sym]:
            stats["busy_symbol"] += 1
            continue

        idx = _idx_at(cs, armed)
        if idx is None:
            continue
        atr = wilder_atr(cs, idx)
        if not atr or atr <= 0:
            stats["no_atr"] += 1
            continue

        is_long = _long(s.direction)
        entry_low = float(s.entry_low or s.reference_price)
        entry_high = float(s.entry_high or s.reference_price)
        ref = entry_low if is_long else entry_high
        if ref <= 0:
            ref = float(s.reference_price)
        stop0 = ref - atr * SL_ATR if is_long else ref + atr * SL_ATR

        stats["armed"] += 1
        arm = arm_retest_entry(
            direction=s.direction,
            arm_time=armed,
            reference_entry=ref,
            original_stop=stop0,
            timeframe=tf,
            candles=cs,
            config=cfg,
        )
        if not arm.filled or arm.fill_price is None or arm.fill_time is None or arm.stop is None:
            stats["no_fill"] += 1
            continue

        stats["filled"] += 1
        qty = _size(
            float(arm.fill_price),
            float(arm.stop),
            risk_usd=risk_usd,
            leverage=leverage,
            max_notional=max_notional,
            margin_per_trade=margin,
        )
        sim = _replay(
            is_long=is_long,
            entry=float(arm.fill_price),
            stop=float(arm.stop),
            qty=qty,
            candles=cs,
            entry_at=ensure_utc(arm.fill_time),
            end_at=ensure_utc(arm.fill_time) + timedelta(hours=48),
            fee_pct=fee,
            move_be=move_be,
        )
        exit_at = sim["exit_at"]
        symbol_free_at[sym] = exit_at
        candidates.append(
            {
                "id": int(s.id),
                "symbol": sym,
                "side": "LONG" if is_long else "SHORT",
                "score": float(s.score),
                "entry_at": ensure_utc(arm.fill_time),
                "exit_at": exit_at,
                "net_pnl": float(sim["pnl"]),
                "r": float(sim["r"]),
                "exit": sim["exit"],
            }
        )

    start_at = min((t["entry_at"] for t in candidates), default=since)
    print(
        f"candidates={len(candidates)} stats={stats}",
        file=sys.stderr,
        flush=True,
    )

    scenarios: list[dict[str, Any]] = []

    def add(name: str, mo: int, md: int, prefer: str | None = None, note: str = "") -> None:
        run = _apply_book(
            candidates,
            start_equity=initial,
            start_at=start_at,
            end_at=now,
            max_open=mo,
            max_per_direction=md,
            prefer=prefer,
        )
        scenarios.append(
            {
                "name": name,
                "max_open": mo,
                "max_per_direction": md,
                "prefer": prefer,
                "note": note,
                **{k: v for k, v in run.items() if k != "daily"},
                "daily": run["daily"],
            }
        )

    # Core sweeps
    add("live_caps_20_12", 20, 12, note="Current paper caps")
    add("caps_20_20", 20, 20, note="Dir-cap not binding")
    add("caps_30_18", 30, 18, note="Modest raise")
    add("caps_40_24", 40, 24, note="Moderate raise")
    add("caps_60_36", 60, 36, note="Loose book")
    add("caps_80_48", 80, 48, note="Very loose")
    add("caps_100_60", 100, 60, note="Near-uncapped capacity")
    add("uncapped", 10_000, 10_000, note="Upper bound")

    # Selection when slots compete
    add("caps_20_12_score", 20, 12, prefer="score", note="Live caps + extreme-score first")
    add("caps_40_24_score", 40, 24, prefer="score", note="Moderate + score selection")
    add("caps_60_36_score", 60, 36, prefer="score", note="Loose + score selection")
    add("caps_20_12_oracle", 20, 12, prefer="pnl", note="ORACLE: live caps + best PnL first")
    add("caps_40_24_oracle", 40, 24, prefer="pnl", note="ORACLE: moderate + best PnL")
    add("caps_60_36_oracle", 60, 36, prefer="pnl", note="ORACLE: loose + best PnL")

    # Score-gated: only strong shorts ≤22 / longs ≥85
    strong = [
        t
        for t in candidates
        if (t["side"] == "SHORT" and t["score"] <= 22)
        or (t["side"] == "LONG" and t["score"] >= 85)
    ]

    def add_subset(name: str, subset: list[dict[str, Any]], mo: int, md: int, note: str) -> None:
        run = _apply_book(
            subset,
            start_equity=initial,
            start_at=start_at,
            end_at=now,
            max_open=mo,
            max_per_direction=md,
        )
        scenarios.append(
            {
                "name": name,
                "max_open": mo,
                "max_per_direction": md,
                "prefer": None,
                "note": note,
                "subset_n": len(subset),
                **{k: v for k, v in run.items() if k != "daily"},
                "daily": run["daily"],
            }
        )

    add_subset("strong_only_uncapped", strong, 10_000, 10_000, "Score≤22 short / ≥85 long, no caps")
    add_subset("strong_only_caps_20", strong, 20, 12, "Strong only + live caps")
    add_subset("strong_only_caps_40", strong, 40, 24, "Strong only + moderate caps")

    # Positive expectancy filter (hindsight — upper diagnostic only)
    winners = [t for t in candidates if float(t["net_pnl"]) > 0]
    add_subset("hindsight_winners_uncapped", winners, 10_000, 10_000, "DIAG only: all winning fills")

    scenarios_sorted = sorted(scenarios, key=lambda x: -float(x["end_equity"]))
    target = 9000.0
    toward = [s for s in scenarios_sorted if float(s["end_equity"]) >= target]

    payload = {
        "generated_at": now.isoformat(),
        "label": "equity_levers_since_reset",
        "geometry": {
            "atr_multiplier": SL_ATR,
            "zone_near": ZONE_NEAR,
            "zone_far": ZONE_FAR,
            "margin": margin,
            "leverage": leverage,
            "since": since.isoformat(),
        },
        "baseline": {
            "initial": initial,
            "live_cash_now": live_equity,
            "fill_candidates": len(candidates),
            "arm_stats": stats,
        },
        "scenarios": scenarios_sorted,
        "toward_9k": [
            {"name": s["name"], "end_equity": s["end_equity"], "accepted_n": s["accepted_n"], "peak_open": s["peak_open"], "note": s["note"]}
            for s in toward
        ],
        "recommendation": _recommend(scenarios_sorted, initial),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print(json.dumps({
        "wrote": str(out),
        "candidates": len(candidates),
        "top5": [
            {"name": s["name"], "equity": s["end_equity"], "n": s["accepted_n"], "peak": s["peak_open"]}
            for s in scenarios_sorted[:5]
        ],
        "live_caps": next(s for s in scenarios if s["name"] == "live_caps_20_12"),
        "toward_9k": payload["toward_9k"],
        "recommendation": payload["recommendation"],
    }, indent=2, default=str))
    return 0


def _recommend(scenarios: list[dict[str, Any]], initial: float) -> dict[str, Any]:
    live = next((s for s in scenarios if s["name"] == "live_caps_20_12"), None)
    unc = next((s for s in scenarios if s["name"] == "uncapped"), None)
    # Best with peak_open <= 40 (somewhat realistic)
    realistic = [s for s in scenarios if int(s.get("peak_open") or 0) <= 40 and "hindsight" not in s["name"]]
    best_real = max(realistic, key=lambda s: float(s["end_equity"])) if realistic else None
    # Best with peak <= 60
    mid = [s for s in scenarios if int(s.get("peak_open") or 0) <= 60 and "hindsight" not in s["name"]]
    best_mid = max(mid, key=lambda s: float(s["end_equity"])) if mid else None
    return {
        "live_caps_equity": live["end_equity"] if live else None,
        "uncapped_equity": unc["end_equity"] if unc else None,
        "best_peak_le_40": {
            "name": best_real["name"],
            "equity": best_real["end_equity"],
            "max_open_setting": best_real["max_open"],
            "peak_open": best_real["peak_open"],
            "note": best_real["note"],
        }
        if best_real
        else None,
        "best_peak_le_60": {
            "name": best_mid["name"],
            "equity": best_mid["end_equity"],
            "max_open_setting": best_mid["max_open"],
            "peak_open": best_mid["peak_open"],
            "note": best_mid["note"],
        }
        if best_mid
        else None,
        "path_to_9k": "Raise max_open until scenario equity crosses $9k; see toward_9k list.",
    }


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
