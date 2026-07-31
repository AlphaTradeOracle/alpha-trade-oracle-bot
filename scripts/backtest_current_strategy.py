"""Vollstaendiger Replay der aktuellen Paper-Strategie auf ~6 Monaten / ~300 Coins.

Spiegelt Live-Gates (Stand eff7968):
  STRONG only · Long Score>=75 · Short Score 18–25 · DQ>=60 · RR>=2
  RSI Short>=33 · BTC-Regime 4h · Retest 0.55–1.0 ATR, pending×6, min 2 Bars
  TP 2/4/6R · Scale 50/25/25 · BE nach TP1 · Expiry 24h ab Fill, 48×TF nach TP1
  Early Scratch 8h / 0.5R MFE · Fee 0.05% · Symbol busy · Portfolio 10%/10/6
  Blackout 21:00–01:00 UTC · Circuit Breaker 2 Losses / 24h

Signale: exports/edge_data/regen_signals_live_risk.csv.gz (Produktions-Score)
Kerzen: deep_candles_1h / deep_candles_4h

    python scripts/backtest_current_strategy.py
    python scripts/backtest_current_strategy.py --mode full --out exports/strategy_6m_backtest.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.entry_blackout import is_in_utc_blackout  # noqa: E402
from app.core.enums import SignalDirection  # noqa: E402
from app.core.time import TIMEFRAME_MINUTES  # noqa: E402
from app.indicators.engine import IndicatorEngine  # noqa: E402
from app.signals.regime import MarketRegime, direction_allowed_by_regime, regime_from_indicators  # noqa: E402
from scripts.backtest_exit_structures import AssetSeries, load_series  # noqa: E402
from scripts.regenerate_historical_signals import load_assets  # noqa: E402

DATA_DIR = REPO_ROOT / "exports" / "edge_data"
DEFAULT_SIGNALS = DATA_DIR / "regen_signals_live_risk.csv.gz"

FEE_PERCENT = 0.05
RETEST_ZONE_NEAR = 0.55
RETEST_ZONE_FAR = 1.0
RETEST_PENDING_MULT = 6
RETEST_MIN_BARS = 1
EXPIRY_MULT = 24
EXPIRY_AFTER_TP1_MULT = 48
TP_MULTS = (1.5, 2.5, 4.0)
SCALE = (0.5, 0.25, 0.25)
MIN_DQ = 60.0
MIN_RR = 2.0
LONG_MIN_SCORE = 75.0
SHORT_MAX_SCORE = 25.0
SHORT_MIN_SCORE = 18.0
RSI_SHORT_MIN = 33.0
RSI_LONG_MAX = 75.0
MIN_ADX = 30.0
EARLY_SCRATCH_H = 12.0
EARLY_SCRATCH_MFE_R = 0.5
BLACKOUT = ""  # live: aus
MAX_PORTFOLIO_RISK_PCT = 10.0
MAX_OPEN = 10
MAX_PER_DIR = 6
RISK_PER_TRADE = 50.0
START_EQUITY = 5000.0
CIRCUIT_LOSSES = 2
CIRCUIT_HOURS = 24
NS_PER_HOUR = 3_600_000_000_000
PRIMARY_TF = "1h"
REGIME_TF = "4h"


@dataclass
class Cand:
    asset_id: int
    symbol: str
    ts_ns: int
    score: float
    direction: str
    reference: float
    stop: float
    atr: float
    dq: float
    rr: float
    rsi: float
    adx: float
    atr_pct: float


@dataclass
class Trade:
    symbol: str
    direction: str
    score: float
    armed_ns: int
    fill_ns: int
    exit_ns: int
    entry: float
    stop: float
    net_r: float
    gross_r: float
    fees_r: float
    exit_reason: str
    bars: int
    mae_r: float
    mfe_r: float
    tp_hits: int
    regime: str
    closed: bool


@dataclass
class OpenPos:
    symbol: str
    direction: str
    fill_ns: int
    exit_ns: int
    risk_usd: float


def _ns_to_dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def load_candidates(path: Path) -> tuple[list[Cand], dict[str, int]]:
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    stats: dict[str, int] = {"raw_rows": len(df)}
    mask = df["direction"].isin(["STRONG_LONG", "STRONG_SHORT"])
    stats["strong"] = int(mask.sum())
    df = df.loc[mask].copy()

    long_ok = (df["direction"] == "STRONG_LONG") & (df["score"] >= LONG_MIN_SCORE)
    short_ok = (
        (df["direction"] == "STRONG_SHORT")
        & (df["score"] <= SHORT_MAX_SCORE)
        & (df["score"] > SHORT_MIN_SCORE)
    )
    df = df.loc[long_ok | short_ok]
    stats["score_band"] = len(df)

    df = df.loc[df["data_quality"] >= MIN_DQ]
    stats["dq"] = len(df)
    df = df.loc[df["risk_reward_ratio"] >= MIN_RR]
    stats["rr"] = len(df)
    df = df.loc[df["adx_14"].fillna(0) >= MIN_ADX]
    stats["adx"] = len(df)

    # RSI gates (regen used rsi_short_min=25; live is 33)
    long_rsi = (df["direction"] == "STRONG_LONG") & (df["rsi_14"].fillna(50) <= RSI_LONG_MAX)
    short_rsi = (df["direction"] == "STRONG_SHORT") & (df["rsi_14"].fillna(50) >= RSI_SHORT_MIN)
    df = df.loc[long_rsi | short_rsi]
    stats["rsi"] = len(df)

    df = df.loc[
        df["stop_loss"].notna()
        & df["reference_price"].notna()
        & (df["reference_price"] > 0)
        & (df["atr_value"].fillna(0) > 0)
    ]
    stats["levels"] = len(df)
    df = df.sort_values(["ts", "asset_id"])

    rows: list[Cand] = []
    for r in df.itertuples(index=False):
        rows.append(
            Cand(
                asset_id=int(r.asset_id),
                symbol=str(r.symbol).upper(),
                ts_ns=int(pd.Timestamp(r.ts).value),
                score=float(r.score),
                direction=str(r.direction),
                reference=float(r.reference_price),
                stop=float(r.stop_loss),
                atr=float(r.atr_value),
                dq=float(r.data_quality),
                rr=float(r.risk_reward_ratio),
                rsi=float(r.rsi_14) if pd.notna(r.rsi_14) else float("nan"),
                adx=float(r.adx_14) if pd.notna(r.adx_14) else float("nan"),
                atr_pct=float(r.atr_percent) if pd.notna(r.atr_percent) else float("nan"),
            )
        )
    stats["candidates"] = len(rows)
    return rows, stats


def build_btc_regime_map(
    series_4h: dict[int, AssetSeries],
    assets: pd.DataFrame,
    scan_times_ns: list[int],
) -> dict[int, MarketRegime | None]:
    """Regime je Scan-Timestamp aus BTC 4h-Indikatoren (wie live)."""
    btc_ids = assets.loc[assets["symbol"].str.upper() == "BTCUSDT", "asset_id"]
    if btc_ids.empty:
        return {t: None for t in scan_times_ns}
    btc_id = int(btc_ids.iloc[0])
    series = series_4h.get(btc_id)
    if series is None:
        return {t: None for t in scan_times_ns}

    engine = IndicatorEngine()
    out: dict[int, MarketRegime | None] = {}
    cache: dict[int, MarketRegime | None] = {}
    index = pd.to_datetime(series.t, unit="ns", utc=True)

    for ts_ns in scan_times_ns:
        idx = int(np.searchsorted(series.t, ts_ns, side="right")) - 1
        if idx < 210:
            out[ts_ns] = None
            continue
        if idx in cache:
            out[ts_ns] = cache[idx]
            continue
        start = max(0, idx - 499)
        frame = pd.DataFrame(
            {
                "open": series.o[start : idx + 1],
                "high": series.h[start : idx + 1],
                "low": series.low[start : idx + 1],
                "close": series.c[start : idx + 1],
                "volume": np.ones(idx + 1 - start),
            },
            index=index[start : idx + 1],
        )
        try:
            inds = engine.compute(frame, REGIME_TF, symbol="BTCUSDT", strict=False)
            snap = regime_from_indicators(inds)
            regime = snap.regime if snap.available else None
        except Exception:
            regime = None
        cache[idx] = regime
        out[ts_ns] = regime
    return out


def arm_retest(
    series: AssetSeries,
    *,
    armed_ns: int,
    reference: float,
    original_stop: float,
    is_long: bool,
    pending_ns: int,
    min_bars: int,
    fallback_atr: float = 0.0,
) -> tuple[str, float, int, float]:
    sig_idx = int(np.searchsorted(series.t, armed_ns, side="right")) - 1
    if sig_idx < 0:
        return "skipped_no_history", 0.0, -1, 0.0
    atr = float(series.atr[sig_idx])
    if not np.isfinite(atr) or atr <= 0:
        atr = float(fallback_atr)
    if not np.isfinite(atr) or atr <= 0:
        return "skipped_no_atr", 0.0, -1, 0.0

    near = atr * RETEST_ZONE_NEAR
    far = atr * RETEST_ZONE_FAR
    zone_lo, zone_hi = (
        (reference - far, reference - near) if is_long else (reference + near, reference + far)
    )
    pending_until = armed_ns + pending_ns
    bars_in_zone = 0

    for i in range(sig_idx + 1, len(series.t)):
        if series.t[i] > pending_until:
            return "skipped_expiry", 0.0, -1, atr
        high = float(series.h[i])
        low = float(series.low[i])
        if (is_long and low <= original_stop) or ((not is_long) and high >= original_stop):
            return "skipped_sl", 0.0, -1, atr
        if low <= zone_hi and high >= zone_lo:
            bars_in_zone += 1
            if bars_in_zone >= max(1, min_bars):
                fill = min(high, zone_hi) if is_long else max(low, zone_lo)
                return "filled", float(fill), i, atr
        else:
            bars_in_zone = 0
    return "skipped_expiry", 0.0, -1, atr


def replay_exit(
    series: AssetSeries,
    *,
    start_idx: int,
    entry: float,
    stop: float,
    is_long: bool,
    entry_atr: float,
    fill_ns: int,
    early_scratch: bool,
) -> tuple[float, float, str, int, int, float, float, int, bool]:
    sign = 1.0 if is_long else -1.0
    r_dist = abs(entry - stop)
    if r_dist <= 0:
        return 0.0, 0.0, "invalid_r", start_idx, 0, 0.0, 0.0, 0, False

    fee_k = (FEE_PERCENT / 100.0) / r_dist
    remaining = 1.0
    gross_r = 0.0
    fees_r = entry * fee_k
    current_stop = stop
    tp_prices = [entry + sign * m * r_dist for m in TP_MULTS]
    tp_done = [False, False, False]
    tp_hits = 0
    exit_reason = "open"
    exit_idx = start_idx
    bars = 0
    mae_r = 0.0
    mfe_r = 0.0
    closed = False
    tf_ns = int(TIMEFRAME_MINUTES[PRIMARY_TF] * 60 * 1_000_000_000)
    expiry_ns = fill_ns + EXPIRY_MULT * tf_ns
    scratch_ns = fill_ns + int(EARLY_SCRATCH_H * NS_PER_HOUR)

    def take(price: float, fraction: float, reason: str, idx: int) -> None:
        nonlocal remaining, gross_r, fees_r, exit_reason, exit_idx, closed
        qty = min(fraction, remaining)
        if qty <= 0:
            return
        gross_r += (price - entry) * sign * qty / r_dist
        fees_r += price * qty * fee_k
        remaining -= qty
        exit_reason = reason
        exit_idx = idx
        if remaining <= 1e-9:
            remaining = 0.0
            closed = True

    for i in range(start_idx, len(series.t)):
        if remaining <= 0:
            break
        bars += 1
        high = float(series.h[i])
        low = float(series.low[i])
        close = float(series.c[i])
        bar_ns = int(series.t[i])

        worst, best = (low, high) if is_long else (high, low)
        mae_r = min(mae_r, (worst - entry) * sign / r_dist)
        mfe_r = max(mfe_r, (best - entry) * sign / r_dist)

        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            reason = "break_even" if abs(current_stop - entry) < 1e-12 else "stop_loss"
            if abs(current_stop - stop) > 1e-12 and abs(current_stop - entry) >= 1e-12:
                reason = "trailing_stop"
            take(current_stop, remaining, reason, i)
            break

        fav = best
        for level, tp_price in enumerate(tp_prices):
            if tp_done[level]:
                continue
            hit = fav >= tp_price if is_long else fav <= tp_price
            if not hit:
                break
            fraction = SCALE[level] if level < 2 else remaining
            if level == 2:
                fraction = remaining
            take(tp_price, fraction, f"take_profit_{level + 1}", i)
            tp_done[level] = True
            tp_hits += 1
            if level == 0:
                current_stop = entry
                expiry_ns = fill_ns + EXPIRY_AFTER_TP1_MULT * tf_ns
            if remaining <= 0:
                break
        if remaining <= 0:
            break

        # Early scratch: after N hours, no TP1, MFE < threshold → close at close
        if (
            early_scratch
            and not tp_done[0]
            and bar_ns >= scratch_ns
            and mfe_r < EARLY_SCRATCH_MFE_R
        ):
            take(close, remaining, "early_scratch", i)
            break

        if bar_ns >= expiry_ns:
            take(close, remaining, "expired", i)
            break

    if remaining > 0:
        last = len(series.t) - 1
        take(float(series.c[last]), remaining, "data_end_mtm", last)
        closed = False

    return gross_r, fees_r, exit_reason, exit_idx, bars, mae_r, mfe_r, tp_hits, closed


def summarize(trades: list[Trade], skips: dict[str, int], meta: dict[str, Any]) -> dict[str, Any]:
    closed = [t for t in trades if t.closed]
    rs = np.array([t.net_r for t in closed], dtype=float) if closed else np.array([])
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    total_r = float(rs.sum()) if len(rs) else 0.0
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float("inf")
    wr = float((rs > 0).mean() * 100) if len(rs) else 0.0

    by_side: dict[str, Any] = {}
    for side, prefix in (("long", "LONG"), ("short", "SHORT")):
        xs = [t for t in closed if prefix in t.direction]
        arr = np.array([t.net_r for t in xs], dtype=float) if xs else np.array([])
        by_side[side] = {
            "n": len(xs),
            "wr": round(float((arr > 0).mean() * 100), 1) if len(arr) else 0.0,
            "total_r": round(float(arr.sum()), 3) if len(arr) else 0.0,
            "expectancy_r": round(float(arr.mean()), 4) if len(arr) else 0.0,
        }

    exits: dict[str, dict[str, float | int]] = {}
    for t in closed:
        e = exits.setdefault(t.exit_reason, {"n": 0, "r": 0.0})
        e["n"] = int(e["n"]) + 1
        e["r"] = float(e["r"]) + t.net_r

    # Monthly
    monthly: list[dict[str, Any]] = []
    if closed:
        frame = pd.DataFrame(
            {
                "month": [pd.Timestamp(t.fill_ns, unit="ns", tz="UTC").strftime("%Y-%m") for t in closed],
                "r": [t.net_r for t in closed],
            }
        )
        for month, g in frame.groupby("month"):
            arr = g["r"].to_numpy()
            monthly.append(
                {
                    "month": month,
                    "n": int(len(arr)),
                    "wr": round(float((arr > 0).mean() * 100), 1),
                    "total_r": round(float(arr.sum()), 3),
                    "expectancy_r": round(float(arr.mean()), 4),
                }
            )

    # Equity curve (chronological)
    ordered = sorted(closed, key=lambda t: t.exit_ns)
    equity_r = 0.0
    peak = 0.0
    max_dd = 0.0
    curve: list[dict[str, Any]] = []
    for i, t in enumerate(ordered):
        equity_r += t.net_r
        peak = max(peak, equity_r)
        max_dd = min(max_dd, equity_r - peak)
        if i % max(1, len(ordered) // 80) == 0 or i == len(ordered) - 1:
            curve.append(
                {
                    "i": i + 1,
                    "date": _ns_to_dt(t.exit_ns).strftime("%Y-%m-%d"),
                    "equity_r": round(equity_r, 3),
                }
            )

    top = sorted(closed, key=lambda t: t.net_r, reverse=True)[:12]
    bot = sorted(closed, key=lambda t: t.net_r)[:12]

    def _row(t: Trade) -> list[str]:
        return [
            t.symbol,
            t.direction.replace("STRONG_", ""),
            f"{t.score:.1f}",
            f"{t.net_r:+.2f}R",
            t.exit_reason,
            t.regime,
            _ns_to_dt(t.fill_ns).strftime("%Y-%m-%d %H:%M"),
            _ns_to_dt(t.exit_ns).strftime("%Y-%m-%d %H:%M"),
        ]

    return {
        "meta": meta,
        "skips": skips,
        "kpi": {
            "signals_in": meta.get("candidates", 0),
            "fills": len(trades),
            "closed": len(closed),
            "open_mtm": sum(1 for t in trades if not t.closed),
            "total_r": round(total_r, 3),
            "expectancy_r": round(float(rs.mean()), 4) if len(rs) else 0.0,
            "wr": round(wr, 1),
            "pf_r": round(pf, 3) if np.isfinite(pf) else None,
            "avg_win_r": round(float(wins.mean()), 3) if len(wins) else 0.0,
            "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else 0.0,
            "fees_r": round(sum(t.fees_r for t in closed), 3),
            "max_dd_r": round(max_dd, 3),
            "usd_at_50r": round(total_r * RISK_PER_TRADE, 2),
        },
        "by_side": by_side,
        "exits": [
            {"reason": k, "n": int(v["n"]), "r": round(float(v["r"]), 3)}
            for k, v in sorted(exits.items(), key=lambda kv: -int(kv[1]["n"]))
        ],
        "monthly": monthly,
        "equity_curve": curve,
        "top": [_row(t) for t in top],
        "bottom": [_row(t) for t in bot],
        "trades_sample": [_row(t) for t in ordered[:40]],
    }


def run(
    candidates: list[Cand],
    series_1h: dict[int, AssetSeries],
    regime_map: dict[int, MarketRegime | None],
    *,
    mode: str,
) -> tuple[list[Trade], dict[str, int]]:
    use_regime = mode in {"full", "no_portfolio"}
    use_portfolio = mode == "full"
    use_blackout = mode in {"full", "no_portfolio"}
    use_circuit = mode in {"full", "no_portfolio"}
    use_scratch = mode in {"full", "no_portfolio", "core"}

    pending_ns = int(RETEST_PENDING_MULT * TIMEFRAME_MINUTES[PRIMARY_TF] * 60 * 1_000_000_000)
    trades: list[Trade] = []
    skips: dict[str, int] = {}
    busy_until: dict[str, int] = {}
    open_positions: list[OpenPos] = []
    recent_losses: dict[str, list[int]] = {}  # symbol -> exit_ns of losses

    def note(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    def prune_open(now_ns: int) -> None:
        nonlocal open_positions
        open_positions = [p for p in open_positions if p.exit_ns > now_ns]

    for cand in candidates:
        series = series_1h.get(cand.asset_id)
        if series is None:
            note("no_candles")
            continue
        is_long = cand.direction.endswith("LONG")

        if use_blackout and is_in_utc_blackout(_ns_to_dt(cand.ts_ns), BLACKOUT):
            note("blackout")
            continue

        regime = regime_map.get(cand.ts_ns)
        regime_label = regime.value if regime is not None else "unknown"
        if use_regime and regime is not None:
            if not direction_allowed_by_regime(regime, SignalDirection(cand.direction)):
                note("regime_block")
                continue

        if busy_until.get(cand.symbol, -1) >= cand.ts_ns:
            note("symbol_busy")
            continue

        if use_circuit:
            cutoff = cand.ts_ns - int(CIRCUIT_HOURS * NS_PER_HOUR)
            losses = [t for t in recent_losses.get(cand.symbol, []) if t >= cutoff]
            recent_losses[cand.symbol] = losses
            if len(losses) >= CIRCUIT_LOSSES:
                note("circuit_breaker")
                continue

        # Portfolio caps checked at arm time (conservative: risk reserved at arm)
        prune_open(cand.ts_ns)
        if use_portfolio:
            open_risk = sum(p.risk_usd for p in open_positions)
            equity = START_EQUITY + sum(t.net_r for t in trades if t.closed) * RISK_PER_TRADE
            # Approximate equity with closed R only
            if open_risk + RISK_PER_TRADE > equity * (MAX_PORTFOLIO_RISK_PCT / 100.0):
                note("portfolio_risk")
                continue
            if len(open_positions) >= MAX_OPEN:
                note("max_open")
                continue
            dir_count = sum(
                1
                for p in open_positions
                if (p.direction.endswith("LONG") == is_long)
            )
            if dir_count >= MAX_PER_DIR:
                note("max_per_direction")
                continue

        # Validate stop side
        if (is_long and cand.stop >= cand.reference) or ((not is_long) and cand.stop <= cand.reference):
            note("bad_stop")
            continue

        status, fill, fill_idx, atr = arm_retest(
            series,
            armed_ns=cand.ts_ns,
            reference=cand.reference,
            original_stop=cand.stop,
            is_long=is_long,
            pending_ns=pending_ns,
            min_bars=RETEST_MIN_BARS,
            fallback_atr=cand.atr,
        )
        if status != "filled":
            note(status)
            continue

        r_dist = abs(cand.reference - cand.stop)
        entry = fill
        stop = entry - r_dist if is_long else entry + r_dist
        fill_ns = int(series.t[fill_idx])

        # Portfolio / blackout at fill time too
        if use_blackout and is_in_utc_blackout(_ns_to_dt(fill_ns), BLACKOUT):
            note("blackout_at_fill")
            continue
        prune_open(fill_ns)
        if use_portfolio:
            open_risk = sum(p.risk_usd for p in open_positions)
            equity = START_EQUITY + sum(t.net_r for t in trades if t.closed and t.exit_ns <= fill_ns) * RISK_PER_TRADE
            if open_risk + RISK_PER_TRADE > equity * (MAX_PORTFOLIO_RISK_PCT / 100.0):
                note("portfolio_risk_fill")
                continue
            if len(open_positions) >= MAX_OPEN:
                note("max_open_fill")
                continue

        gross_r, fees_r, reason, exit_idx, bars, mae_r, mfe_r, tp_hits, closed = replay_exit(
            series,
            start_idx=fill_idx,
            entry=entry,
            stop=stop,
            is_long=is_long,
            entry_atr=atr if atr > 0 else cand.atr,
            fill_ns=fill_ns,
            early_scratch=use_scratch,
        )
        exit_ns = int(series.t[exit_idx])
        net_r = gross_r - fees_r
        trade = Trade(
            symbol=cand.symbol,
            direction=cand.direction,
            score=cand.score,
            armed_ns=cand.ts_ns,
            fill_ns=fill_ns,
            exit_ns=exit_ns,
            entry=entry,
            stop=stop,
            net_r=net_r,
            gross_r=gross_r,
            fees_r=fees_r,
            exit_reason=reason,
            bars=bars,
            mae_r=mae_r,
            mfe_r=mfe_r,
            tp_hits=tp_hits,
            regime=regime_label,
            closed=closed,
        )
        trades.append(trade)
        busy_until[cand.symbol] = exit_ns
        open_positions.append(
            OpenPos(
                symbol=cand.symbol,
                direction=cand.direction,
                fill_ns=fill_ns,
                exit_ns=exit_ns,
                risk_usd=RISK_PER_TRADE,
            )
        )
        if closed and net_r <= 0:
            recent_losses.setdefault(cand.symbol, []).append(exit_ns)

    return trades, skips


def write_canvas(result: dict[str, Any], path: Path) -> None:
    skips = result["skips"]
    skip_table = [[k, str(v)] for k, v in sorted(skips.items(), key=lambda kv: -kv[1])[:20]]
    monthly_rows = [
        [
            m["month"],
            str(m["n"]),
            f"{m['wr']}%",
            f"{m['total_r']:+.2f}R",
            f"{m['expectancy_r']:+.3f}R",
        ]
        for m in result["monthly"]
    ]
    comp_rows: list[list[str]] = []
    for mode, block in (result.get("comparisons") or {}).items():
        k = block["kpi"]
        comp_rows.append(
            [
                mode,
                str(k["closed"]),
                f"{k['wr']}%",
                f"{k['total_r']:+.2f}R",
                f"{k['expectancy_r']:+.3f}R",
                str(k["pf_r"]),
                f"{k['max_dd_r']}R",
                f"${k['usd_at_50r']}",
            ]
        )
    payload = {
        "KPI": result["kpi"],
        "SIDE": result["by_side"],
        "MONTHLY": result["monthly"],
        "MONTHLY_ROWS": monthly_rows,
        "EXITS": result["exits"],
        "CURVE": result["equity_curve"],
        "META": result["meta"],
        "SKIP_ROWS": skip_table,
        "TOP": result["top"],
        "BOTTOM": result["bottom"],
        "COMP_ROWS": comp_rows,
    }
    # Embed JSON literals via placeholders — avoids f-string/JSX brace wars.
    body = """import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  LineChart,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const KPI = __KPI__ as const;
const SIDE = __SIDE__ as const;
const MONTHLY = __MONTHLY__ as const;
const MONTHLY_ROWS = __MONTHLY_ROWS__ as const;
const EXITS = __EXITS__ as const;
const CURVE = __CURVE__ as const;
const META = __META__ as const;
const SKIP_ROWS = __SKIP_ROWS__ as const;
const TOP = __TOP__ as const;
const BOTTOM = __BOTTOM__ as const;
const COMP_ROWS = __COMP_ROWS__ as const;
const TRADE_HEADERS = ["Symbol", "Side", "Score", "R", "Exit", "Regime", "Fill UTC", "Exit UTC"];

function toneFor(n: number): "success" | "danger" | undefined {
  if (n > 0) return "success";
  if (n < 0) return "danger";
  return undefined;
}

function fmtR(n: number, digits = 2): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}R`;
}

export default function Strategy6mBacktest() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Current Strategy — 6M Universe Backtest</H1>
        <Text tone="secondary">
          {META.assets} coins · {META.scan_start} → {META.scan_end} · mode={META.mode} · 1h replay · {META.generated}
        </Text>
      </Stack>

      <Callout tone={KPI.total_r >= 0 ? "success" : "danger"}>
        Net {fmtR(KPI.total_r)} on {KPI.closed} closed fills (≈ ${KPI.usd_at_50r} at $50/R) · Expectancy{" "}
        {fmtR(KPI.expectancy_r, 3)} · WR {KPI.wr}% · PF(R) {KPI.pf_r ?? "inf"} · Max DD {KPI.max_dd_r}R
      </Callout>

      <Callout tone="warning">
        Signals every 4h (regen preset live); production scans every 30m — fewer entries than live. No 15m TF.
        Retest/Exit/Regime/Portfolio match live defaults (eff7968).
      </Callout>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat value={fmtR(KPI.total_r)} label="Total R (net fees)" tone={toneFor(KPI.total_r)} />
        <Stat value={fmtR(KPI.expectancy_r, 3)} label="Expectancy / Trade" tone={toneFor(KPI.expectancy_r)} />
        <Stat value={`${KPI.wr}%`} label={`Win Rate · PF ${KPI.pf_r ?? "inf"}`} tone="info" />
        <Stat value={`${KPI.max_dd_r}R`} label="Max Drawdown (R)" tone="danger" />
      </Grid>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat value={String(KPI.signals_in)} label="Candidates after gates" />
        <Stat value={`${KPI.fills} / ${KPI.closed}`} label="Fills / Closed" tone="info" />
        <Stat
          value={`${SIDE.long.wr}%`}
          label={`LONG ${SIDE.long.n} · ${fmtR(SIDE.long.total_r)}`}
          tone={toneFor(SIDE.long.total_r)}
        />
        <Stat
          value={`${SIDE.short.wr}%`}
          label={`SHORT ${SIDE.short.n} · ${fmtR(SIDE.short.total_r)}`}
          tone={toneFor(SIDE.short.total_r)}
        />
      </Grid>

      <Stack gap={8}>
        <H2>Strategy Gates (live parity)</H2>
        <Row gap={8} wrap>
          {META.pills.map((p: string) => (
            <Pill key={p} tone="info">
              {p}
            </Pill>
          ))}
        </Row>
      </Stack>

      {COMP_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Ablation: Mode Comparison</H2>
          <Table
            headers={["Mode", "Closed", "WR", "Total R", "E[R]", "PF(R)", "Max DD", "~USD@$50/R"]}
            rows={COMP_ROWS}
            striped
            framed
          />
          <Text tone="secondary" size="small">
            full = all live gates · no_portfolio = regime/scratch/blackout without caps · core = retest+TP+scratch only
          </Text>
        </Stack>
      ) : null}

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Equity Curve (cumulative R)</H2>
          <LineChart
            categories={CURVE.map((d) => d.date)}
            series={[{ name: "Equity (R)", data: CURVE.map((d) => d.equity_r) }]}
            height={220}
          />
          <Text tone="secondary" size="small">
            Closed trades only · chronological by exit · offline replay · {META.generated}
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Monthly Total R</H2>
          <BarChart
            categories={MONTHLY.map((d) => d.month)}
            series={[{ name: "Total R", data: MONTHLY.map((d) => d.total_r) }]}
            height={220}
          />
          <Text tone="secondary" size="small">
            Net R by fill month
          </Text>
        </Stack>
      </Grid>

      <Stack gap={8}>
        <H2>Monthly Breakdown</H2>
        <Table
          headers={["Month", "Trades", "WR", "Total R", "Expectancy"]}
          rows={MONTHLY_ROWS}
          striped
          framed
        />
      </Stack>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Exit Mix (count)</H2>
          <BarChart
            categories={EXITS.map((e) => e.reason)}
            series={[{ name: "Trades", data: EXITS.map((e) => e.n) }]}
            height={200}
          />
        </Stack>
        <Stack gap={8}>
          <H2>Exit Mix (R)</H2>
          <BarChart
            categories={EXITS.map((e) => e.reason)}
            series={[{ name: "Net R", data: EXITS.map((e) => e.r) }]}
            beginAtZero={false}
            height={200}
          />
        </Stack>
      </Grid>

      <Row gap={8} wrap>
        {EXITS.map((e) => (
          <Pill key={e.reason} tone={e.r >= 0 ? "success" : "deleted"}>
            {e.reason}: {e.n}× · {fmtR(e.r)}
          </Pill>
        ))}
      </Row>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Top Trades</H2>
          <Table headers={TRADE_HEADERS} rows={TOP} striped framed />
        </Stack>
        <Stack gap={8}>
          <H2>Weakest Trades</H2>
          <Table headers={TRADE_HEADERS} rows={BOTTOM} striped framed />
        </Stack>
      </Grid>

      <Stack gap={8}>
        <H2>Skip Funnel (top reasons)</H2>
        <Table headers={["Reason", "Count"]} rows={SKIP_ROWS} striped framed />
        <Text tone="secondary" size="small">
          Post-candidate rejects during arm/fill/portfolio · backtest_current_strategy.py
        </Text>
      </Stack>

      <Card>
        <CardHeader>Method</CardHeader>
        <CardBody>
          <Text size="small">
            Offline replay of regenerated STRONG signals on deep 1h candles. Retest fill = worst price in
            zone after {META.retest_min_bars} consecutive bars · Fee {META.fee_percent}%/side in R · Runtime{" "}
            {META.runtime_s}s · {META.generated}
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
"""
    repl = {
        "__KPI__": json.dumps(payload["KPI"], indent=2),
        "__SIDE__": json.dumps(payload["SIDE"], indent=2),
        "__MONTHLY__": json.dumps(payload["MONTHLY"], indent=2),
        "__MONTHLY_ROWS__": json.dumps(payload["MONTHLY_ROWS"], indent=2),
        "__EXITS__": json.dumps(payload["EXITS"], indent=2),
        "__CURVE__": json.dumps(payload["CURVE"], indent=2),
        "__META__": json.dumps(payload["META"], indent=2),
        "__SKIP_ROWS__": json.dumps(payload["SKIP_ROWS"], indent=2),
        "__TOP__": json.dumps(payload["TOP"], indent=2),
        "__BOTTOM__": json.dumps(payload["BOTTOM"], indent=2),
        "__COMP_ROWS__": json.dumps(payload["COMP_ROWS"], indent=2),
    }
    for key, value in repl.items():
        body = body.replace(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument(
        "--mode",
        choices=("full", "no_portfolio", "core"),
        default="full",
        help="full=all live gates; no_portfolio=no caps; core=retest+TP+scratch only",
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "exports" / "strategy_6m_backtest.json")
    parser.add_argument(
        "--canvas",
        type=Path,
        default=Path(
            r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot"
            r"\canvases\strategy-6m-backtest.canvas.tsx"
        ),
    )
    parser.add_argument("--also-modes", action="store_true", help="Run no_portfolio + core for comparison")
    args = parser.parse_args(argv)

    t0 = time.time()
    print("Loading candidates...", file=sys.stderr, flush=True)
    candidates, gate_stats = load_candidates(args.signals)
    print(f"  gate funnel: {gate_stats}", file=sys.stderr, flush=True)

    print("Loading 1h + 4h candles...", file=sys.stderr, flush=True)
    series_1h = load_series(args.data_dir, PRIMARY_TF)
    series_4h = load_series(args.data_dir, REGIME_TF)
    assets = load_assets(args.data_dir)

    scan_times = sorted({c.ts_ns for c in candidates})
    print(f"Building BTC regime map for {len(scan_times)} scan times...", file=sys.stderr, flush=True)
    regime_map = build_btc_regime_map(series_4h, assets, scan_times)
    regime_counts = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for r in regime_map.values():
        if r is None:
            regime_counts["unknown"] += 1
        else:
            regime_counts[r.value] += 1
    print(f"  regime: {regime_counts}", file=sys.stderr, flush=True)

    modes = [args.mode]
    if args.also_modes:
        for m in ("full", "no_portfolio", "core"):
            if m not in modes:
                modes.append(m)

    all_results: dict[str, Any] = {}
    primary: dict[str, Any] | None = None

    for mode in modes:
        print(f"Running mode={mode} on {len(candidates)} candidates...", file=sys.stderr, flush=True)
        trades, skips = run(candidates, series_1h, regime_map, mode=mode)
        meta = {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "mode": mode,
            "signal_file": str(args.signals.name),
            "assets": int(assets["asset_id"].nunique()),
            "scan_start": _ns_to_dt(min(c.ts_ns for c in candidates)).isoformat() if candidates else "",
            "scan_end": _ns_to_dt(max(c.ts_ns for c in candidates)).isoformat() if candidates else "",
            "candidates": len(candidates),
            "gate_funnel": gate_stats,
            "regime_counts": regime_counts,
            "fee_percent": FEE_PERCENT,
            "retest_min_bars": RETEST_MIN_BARS,
            "runtime_s": round(time.time() - t0, 1),
            "pills": [
                "STRONG only",
                f"Long≥{LONG_MIN_SCORE:.0f} / Short {SHORT_MIN_SCORE:.0f}–{SHORT_MAX_SCORE:.0f}",
                f"RSI Short≥{RSI_SHORT_MIN:.0f}",
                "BTC regime 4h",
                f"Retest {RETEST_ZONE_NEAR}–{RETEST_ZONE_FAR}×ATR · ×{RETEST_PENDING_MULT} · {RETEST_MIN_BARS} bars",
                "TP 2/4/6R · 50/25/25",
                f"Early scratch {EARLY_SCRATCH_H:.0f}h/{EARLY_SCRATCH_MFE_R}R",
                f"Portfolio {MAX_PORTFOLIO_RISK_PCT:.0f}%/{MAX_OPEN}/{MAX_PER_DIR}"
                if mode == "full"
                else "Portfolio off",
                f"Blackout {BLACKOUT}",
                f"Circuit {CIRCUIT_LOSSES}L/{CIRCUIT_HOURS}h",
            ],
        }
        result = summarize(trades, skips, meta)
        all_results[mode] = {
            "kpi": result["kpi"],
            "by_side": result["by_side"],
            "exits": result["exits"],
            "monthly": result["monthly"],
            "skips": skips,
        }
        print(
            f"  {mode}: closed={result['kpi']['closed']} totalR={result['kpi']['total_r']:+.2f} "
            f"E[R]={result['kpi']['expectancy_r']:+.3f} WR={result['kpi']['wr']}% "
            f"PF={result['kpi']['pf_r']} DD={result['kpi']['max_dd_r']}",
            file=sys.stderr,
            flush=True,
        )
        if mode == args.mode:
            primary = result

    assert primary is not None
    primary["comparisons"] = all_results

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Compact JSON (no full trade list — keep sample in summary)
    export = {
        **primary,
        "comparisons": all_results,
    }
    args.out.write_text(json.dumps(export, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr, flush=True)

    write_canvas(primary, args.canvas)
    print(f"Wrote {args.canvas}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
