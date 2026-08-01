#!/usr/bin/env python3
"""Counterfactual: old paper gates vs WUSDT-lesson skip rules.

Universe = historical closed paper trades (canvas CLOSED_ROWS) + live DB
closed positions. Features come from matched signals / score components /
indicator snapshots.

Filtered trades contribute $0; taken trades keep recorded realized PnL.

Usage:
  .venv/bin/python scripts/simulate_lesson_filters.py \\
      > exports/lesson_filters_sim.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
CANVAS = ROOT / "canvases" / "paper-trades-performance.canvas.tsx"


@dataclass
class Trade:
    position_id: int
    symbol: str
    side: str
    pnl: float
    exit_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    signal_id: int | None = None
    score: float | None = None
    is_long: bool = False
    # lesson features (None = unknown → do not skip on that axis)
    bullish_divergence: bool | None = None
    volume_ratio: float | None = None
    rsi_rising: bool | None = None
    bb_squeeze: bool | None = None
    weak_breakdown_volume: bool | None = None
    no_volume_confirmation: bool | None = None
    matched: bool = False


@dataclass
class VariantResult:
    key: str
    label: str
    group: str
    n_taken: int
    n_skipped: int
    total_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    avg_pnl: float
    skip_reasons: dict[str, int] = field(default_factory=dict)
    delta_pnl_vs_old: float | None = None
    delta_wr_vs_old: float | None = None
    delta_pf_vs_old: float | None = None
    skipped_trade_ids: list[int] = field(default_factory=list)


def dsn() -> str:
    load_dotenv(ROOT / ".env")
    if url := os.getenv("DATABASE_URL"):
        return url
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ['POSTGRES_HOST']}:{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.environ['POSTGRES_DB']}"
    )


def _parse_pnl(raw: str) -> float:
    return float(raw.replace("$", "").replace(",", "").replace("+", ""))


def _parse_dt(raw: str | datetime | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    return datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def _load_canvas_closed() -> list[Trade]:
    """Parse CLOSED_ROWS from the performance canvas (two historical layouts)."""
    if not CANVAS.exists():
        return []
    text = CANVAS.read_text(encoding="utf-8")
    marker = "const CLOSED_ROWS = "
    start = text.index(marker) + len(marker)
    end = text.index("] as const", start) + 1
    rows = json.loads(text[start:end])
    out: list[Trade] = []
    for row in rows:
        side = str(row[2]).upper()
        # Layout A (compact): id,symbol,side,rpnl,exit,opened,closed
        # Layout B (full): id,symbol,side,status,entry,sl,tp1,tp2,tp3,...,rpnl,exit,opened,closed
        if str(row[3]).lower() == "closed" or (
            isinstance(row[3], str) and "$" not in str(row[3]) and len(row) >= 12
        ):
            # find pnl column: first field containing '$'
            pnl_idx = next(i for i, v in enumerate(row) if isinstance(v, str) and "$" in v)
            exit_idx = pnl_idx + 1
            opened_idx = pnl_idx + 2
            closed_idx = pnl_idx + 3
            pnl = _parse_pnl(str(row[pnl_idx]))
            exit_reason = str(row[exit_idx])
            opened = str(row[opened_idx])
            closed = str(row[closed_idx]) if closed_idx < len(row) else None
        else:
            pnl = _parse_pnl(str(row[3]))
            exit_reason = str(row[4])
            opened = str(row[5])
            closed = str(row[6])

        out.append(
            Trade(
                position_id=int(row[0]),
                symbol=str(row[1]),
                side=side,
                pnl=pnl,
                exit_reason=exit_reason,
                opened_at=_parse_dt(opened),  # type: ignore[arg-type]
                closed_at=_parse_dt(closed),
                is_long="LONG" in side,
            )
        )
    return out


def _load_db_closed(cur) -> list[Trade]:
    cur.execute(
        """
        SELECT id, symbol, direction, realized_pnl, exit_reason,
               opened_at, closed_at, signal_id
        FROM paper_positions
        WHERE status = 'closed'
        ORDER BY id
        """
    )
    out: list[Trade] = []
    for row in cur.fetchall():
        side = str(row["direction"]).upper()
        out.append(
            Trade(
                position_id=int(row["id"]),
                symbol=row["symbol"],
                side=side,
                pnl=float(row["realized_pnl"] or 0),
                exit_reason=row["exit_reason"],
                opened_at=_parse_dt(row["opened_at"]),  # type: ignore[arg-type]
                closed_at=_parse_dt(row["closed_at"]),
                signal_id=int(row["signal_id"]) if row["signal_id"] else None,
                is_long="LONG" in side,
            )
        )
    return out


def _merge_universe(canvas: list[Trade], live: list[Trade]) -> list[Trade]:
    by_id = {t.position_id: t for t in canvas}
    for t in live:
        by_id[t.position_id] = t  # live wins on overlap
    trades = sorted(by_id.values(), key=lambda t: t.opened_at)
    return trades


def _match_and_enrich(cur, trades: list[Trade]) -> None:
    for trade in trades:
        sig = None
        if trade.signal_id:
            cur.execute(
                """
                SELECT s.id, s.score, s.direction, s.counter_arguments,
                       s.created_at, s.asset_id, s.primary_timeframe
                FROM signals s WHERE s.id = %s
                """,
                (trade.signal_id,),
            )
            sig = cur.fetchone()

        if sig is None:
            cur.execute(
                """
                SELECT s.id, s.score, s.direction, s.counter_arguments,
                       s.created_at, s.asset_id, s.primary_timeframe
                FROM signals s
                JOIN assets a ON a.id = s.asset_id
                WHERE a.symbol = %s
                  AND (
                    (%s AND s.direction IN ('SHORT', 'STRONG_SHORT'))
                    OR (%s AND s.direction IN ('LONG', 'STRONG_LONG'))
                  )
                  AND s.created_at BETWEEN %s AND %s
                  AND s.stop_loss IS NOT NULL
                ORDER BY abs(extract(epoch from (s.created_at - %s))) ASC
                LIMIT 1
                """,
                (
                    trade.symbol,
                    not trade.is_long,
                    trade.is_long,
                    trade.opened_at - timedelta(hours=36),
                    trade.opened_at + timedelta(hours=2),
                    trade.opened_at,
                ),
            )
            sig = cur.fetchone()

        if sig is None:
            continue

        trade.matched = True
        trade.signal_id = int(sig["id"])
        trade.score = float(sig["score"]) if sig["score"] is not None else trade.score

        cur.execute(
            """
            SELECT category, detail
            FROM signal_score_components
            WHERE signal_id = %s
            """,
            (trade.signal_id,),
        )
        details = " | ".join((r["detail"] or "") for r in cur.fetchall())
        counters = " | ".join(sig["counter_arguments"] or [])
        blob = f"{counters} {details}".lower()

        trade.bullish_divergence = (
            "bullische divergenz" in blob or "bullish divergence" in blob
        )
        trade.rsi_rising = "rsi steigend" in blob or "rsi rising" in blob
        trade.bb_squeeze = "bollinger-squeeze" in blob or "bollinger squeeze" in blob
        trade.weak_breakdown_volume = (
            "unterdurchschnittliches volumen" in blob
            or "below-average volume" in blob
        )
        # Stricter: breakdown explicitly without volume confirmation
        trade.no_volume_confirmation = (
            "ohne volumenbestaetigung" in blob
            or "without volume confirmation" in blob
        )

        tf = sig["primary_timeframe"] or "1h"
        cur.execute(
            """
            SELECT volume_ratio, bb_width, rsi_14, candle_open_time
            FROM indicator_snapshots
            WHERE asset_id = %s
              AND timeframe = %s
              AND candle_open_time <= %s
            ORDER BY candle_open_time DESC
            LIMIT 1
            """,
            (sig["asset_id"], tf, trade.opened_at),
        )
        snap = cur.fetchone()
        if snap and snap["volume_ratio"] is not None:
            trade.volume_ratio = float(snap["volume_ratio"])

        # Approximate BB squeeze from stored widths when note missing
        if trade.bb_squeeze is False and snap and snap["bb_width"] is not None:
            cur.execute(
                """
                SELECT avg(bb_width) AS avg_width
                FROM (
                  SELECT bb_width
                  FROM indicator_snapshots
                  WHERE asset_id = %s
                    AND timeframe = %s
                    AND candle_open_time <= %s
                    AND bb_width IS NOT NULL
                  ORDER BY candle_open_time DESC
                  LIMIT 50
                ) recent
                """,
                (sig["asset_id"], tf, trade.opened_at),
            )
            avg_row = cur.fetchone()
            avg_w = float(avg_row["avg_width"]) if avg_row and avg_row["avg_width"] else None
            width = float(snap["bb_width"])
            if avg_w and avg_w > 0 and (width / avg_w) < 0.7:
                trade.bb_squeeze = True


def _profit_factor(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(abs(p) for p in pnls if p < 0)
    if losses > 0:
        return round(wins / losses, 4)
    return 99.0 if wins > 0 else 0.0


def _max_drawdown(items: list[tuple[datetime, float]]) -> float:
    if not items:
        return 0.0
    cum = peak = max_dd = 0.0
    for _, pnl in sorted(items, key=lambda x: x[0]):
        cum += pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def _evaluate(trades: list[Trade], key: str, label: str, group: str, gate) -> VariantResult:
    taken: list[float] = []
    by_time: list[tuple[datetime, float]] = []
    skips: dict[str, int] = {}
    skipped_ids: list[int] = []

    for trade in trades:
        ok, reason = gate(trade)
        if not ok:
            skips[reason or "filtered"] = skips.get(reason or "filtered", 0) + 1
            skipped_ids.append(trade.position_id)
            continue
        taken.append(trade.pnl)
        by_time.append((trade.closed_at or trade.opened_at, trade.pnl))

    n = len(taken)
    wins = sum(1 for p in taken if p > 0)
    total = round(sum(taken), 2)
    return VariantResult(
        key=key,
        label=label,
        group=group,
        n_taken=n,
        n_skipped=len(trades) - n,
        total_pnl=total,
        win_rate=round(wins / n, 4) if n else 0.0,
        profit_factor=_profit_factor(taken),
        max_drawdown=_max_drawdown(by_time),
        avg_pnl=round(total / n, 2) if n else 0.0,
        skip_reasons=skips,
        skipped_trade_ids=skipped_ids,
    )


def _gate_old(trade: Trade) -> tuple[bool, str | None]:
    """Old rules: trade was taken → keep (on matched/evaluated universe)."""
    return True, None


def _gate_factory(rules: set[str]):
    def gate(trade: Trade) -> tuple[bool, str | None]:
        if not trade.is_long:
            if "bullish_div" in rules and trade.bullish_divergence is True:
                return False, "short_bullish_divergence"
            if "vol_lt_0_5" in rules and trade.volume_ratio is not None and trade.volume_ratio < 0.5:
                return False, "short_volume_ratio_lt_0_5"
            if "weak_vol" in rules and trade.weak_breakdown_volume is True:
                return False, "short_weak_breakdown_volume"
            if "no_vol_confirm" in rules and trade.no_volume_confirmation is True:
                return False, "short_no_volume_confirmation"
            if "rsi_rising" in rules and trade.rsi_rising is True:
                return False, "short_rsi_rising"
        if "bb_squeeze" in rules and trade.bb_squeeze is True:
            return False, "bb_squeeze"
        return True, None

    return gate


VARIANTS: list[tuple[str, str, str, set[str]]] = [
    ("old_rules", "Alte Regeln (Paper wie genommen)", "baseline", set()),
    (
        "new_skip_bullish_div",
        "NEU: Short skip bei bullischer Divergenz",
        "single",
        {"bullish_div"},
    ),
    (
        "new_skip_vol_lt_0_5",
        "NEU: Short skip bei volume_ratio < 0.5",
        "single",
        {"vol_lt_0_5"},
    ),
    (
        "new_skip_weak_vol_text",
        "NEU: Short skip bei unterdurchschnittlichem Volumen (Text)",
        "single",
        {"weak_vol"},
    ),
    (
        "new_skip_no_vol_confirm",
        "NEU: Short skip nur bei Break ohne Volumenbestätigung",
        "single",
        {"no_vol_confirm"},
    ),
    (
        "new_skip_rsi_rising",
        "NEU: Short skip wenn RSI steigt",
        "single",
        {"rsi_rising"},
    ),
    (
        "new_skip_bb_squeeze",
        "NEU: Skip bei Bollinger-Squeeze",
        "single",
        {"bb_squeeze"},
    ),
    (
        "new_combo_core",
        "NEU Kombi: Divergenz + Break-ohne-Vol + RSI steigend",
        "combo",
        {"bullish_div", "no_vol_confirm", "rsi_rising"},
    ),
    (
        "new_combo_strict",
        "NEU Kombi streng: Divergenz + weak-vol-Text + RSI",
        "combo",
        {"bullish_div", "weak_vol", "rsi_rising"},
    ),
    (
        "new_combo_full",
        "NEU Kombi full: alle Lesson-Filter",
        "combo",
        {
            "bullish_div",
            "weak_vol",
            "no_vol_confirm",
            "rsi_rising",
            "vol_lt_0_5",
            "bb_squeeze",
        },
    ),
]


def main() -> int:
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            canvas = _load_canvas_closed()
            live = _load_db_closed(cur)
            trades = _merge_universe(canvas, live)
            _match_and_enrich(cur, trades)

    matched = [t for t in trades if t.matched]
    unmatched = [t for t in trades if not t.matched]

    results: list[VariantResult] = []
    for key, label, group, rules in VARIANTS:
        gate = _gate_old if not rules else _gate_factory(rules)
        results.append(_evaluate(matched, key, label, group, gate))

    old = next(r for r in results if r.key == "old_rules")
    for r in results:
        r.delta_pnl_vs_old = round(r.total_pnl - old.total_pnl, 2)
        r.delta_wr_vs_old = round(r.win_rate - old.win_rate, 4)
        r.delta_pf_vs_old = round(r.profit_factor - old.profit_factor, 4)

    feature_coverage = {
        "matched": len(matched),
        "unmatched": len(unmatched),
        "bullish_divergence_true": sum(1 for t in matched if t.bullish_divergence),
        "rsi_rising_true": sum(1 for t in matched if t.rsi_rising),
        "bb_squeeze_true": sum(1 for t in matched if t.bb_squeeze),
        "weak_breakdown_volume_true": sum(1 for t in matched if t.weak_breakdown_volume),
        "no_volume_confirmation_true": sum(1 for t in matched if t.no_volume_confirmation),
        "volume_ratio_lt_0_5": sum(
            1 for t in matched if t.volume_ratio is not None and t.volume_ratio < 0.5
        ),
        "with_volume_ratio": sum(1 for t in matched if t.volume_ratio is not None),
    }

    # Per-trade attribution for the full combo
    full = next(r for r in results if r.key == "new_combo_full")
    skipped_set = set(full.skipped_trade_ids)
    attribution = []
    for t in matched:
        reasons = []
        if t.position_id in skipped_set:
            hits = []
            if not t.is_long and t.bullish_divergence:
                hits.append("bullish_div")
            if not t.is_long and t.volume_ratio is not None and t.volume_ratio < 0.5:
                hits.append("vol_lt_0_5")
            if not t.is_long and t.weak_breakdown_volume:
                hits.append("weak_vol")
            if not t.is_long and t.no_volume_confirmation:
                hits.append("no_vol_confirm")
            if not t.is_long and t.rsi_rising:
                hits.append("rsi_rising")
            if t.bb_squeeze:
                hits.append("bb_squeeze")
            reasons = hits
        attribution.append(
            {
                "id": t.position_id,
                "symbol": t.symbol,
                "side": t.side,
                "pnl": t.pnl,
                "exit": t.exit_reason,
                "score": t.score,
                "taken_under_full_new": t.position_id not in skipped_set,
                "skip_hits": reasons,
                "features": {
                    "bullish_divergence": t.bullish_divergence,
                    "volume_ratio": t.volume_ratio,
                    "rsi_rising": t.rsi_rising,
                    "bb_squeeze": t.bb_squeeze,
                    "weak_breakdown_volume": t.weak_breakdown_volume,
                    "no_volume_confirmation": t.no_volume_confirmation,
                },
                "opened_at": t.opened_at.isoformat(),
            }
        )

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": {
            "type": "paper_lesson_filter_counterfactual",
            "description": (
                "Compare recorded paper outcomes under old take-all rules vs "
                "new WUSDT-lesson skip gates. Skipped trades = $0."
            ),
            "old_rules": "All matched closed paper trades kept (as actually taken).",
            "new_rules": [v[0] for v in VARIANTS if v[0] != "old_rules"],
            "caveats": [
                "Only trades with a matched signal/features are in the comparison set",
                "Early ledger trades before signal retention cannot be feature-matched",
                "PnL is recorded paper outcome, not re-simulated",
                "Small sample — treat as directional evidence, not proof",
                "Feature proxies use score-component / counter text + snapshots",
            ],
        },
        "sample": {
            "n_universe": len(trades),
            "n_matched_evaluated": len(matched),
            "n_unmatched_excluded": len(unmatched),
            "universe_pnl": round(sum(t.pnl for t in trades), 2),
            "matched_pnl": round(sum(t.pnl for t in matched), 2),
            "unmatched_pnl": round(sum(t.pnl for t in unmatched), 2),
            "feature_coverage": feature_coverage,
            "first_open": min((t.opened_at for t in matched), default=None),
            "last_close": max((t.closed_at for t in matched if t.closed_at), default=None),
        },
        "comparison": [asdict(r) for r in results],
        "headline": {
            "old": {
                "n": old.n_taken,
                "pnl": old.total_pnl,
                "wr": old.win_rate,
                "pf": old.profit_factor,
                "max_dd": old.max_drawdown,
            },
            "new_combo_full": {
                "n": full.n_taken,
                "pnl": full.total_pnl,
                "wr": full.win_rate,
                "pf": full.profit_factor,
                "max_dd": full.max_drawdown,
                "delta_pnl": full.delta_pnl_vs_old,
            },
            "best_single": None,
        },
        "trades": attribution,
        "unmatched_ids": [t.position_id for t in unmatched],
    }

    singles = [r for r in results if r.group == "single"]
    if singles:
        best = max(singles, key=lambda r: (r.delta_pnl_vs_old or -1e9, r.profit_factor))
        payload["headline"]["best_single"] = {
            "key": best.key,
            "label": best.label,
            "delta_pnl": best.delta_pnl_vs_old,
            "n_taken": best.n_taken,
            "pnl": best.total_pnl,
            "wr": best.win_rate,
            "pf": best.profit_factor,
        }

    for k in ("first_open", "last_close"):
        v = payload["sample"][k]
        if isinstance(v, datetime):
            payload["sample"][k] = v.isoformat()

    print(json.dumps(payload, indent=2, default=str))

    print("\n=== OLD vs NEW (matched closed paper trades) ===", file=sys.stderr)
    print(
        f"Universe {len(trades)} closed | evaluated {len(matched)} matched | "
        f"excluded unmatched {len(unmatched)}",
        file=sys.stderr,
    )
    for r in results:
        print(
            f"{r.key:28} n={r.n_taken:3} skip={r.n_skipped:3} "
            f"pnl={r.total_pnl:+8.2f} Δ={r.delta_pnl_vs_old:+8.2f} "
            f"WR={r.win_rate:6.1%} PF={r.profit_factor:5.2f} DD={r.max_drawdown:7.2f}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
