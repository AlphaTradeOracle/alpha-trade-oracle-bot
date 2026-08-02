"""Retrospective Market Regime impact on closed paper trades.

Loads BTC MTF candles from Binance public API (no DB), scores regime at each
trade open time, and estimates:
  - hard veto (would trade have been blocked?)
  - soft score blend vs original coin score
  - PnL kept vs removed under veto

Usage:
  python scripts/analyze_paper_regime_counterfactual.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.enums import SignalDirection
from app.market_regime import MarketRegimeEngine, hard_veto_reason
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.types import ScoreWeights

BINANCE = "https://api.binance.com"
OUT_JSON = ROOT / "exports" / "paper_regime_counterfactual.json"
OUT_TXT = ROOT / "exports" / "paper_regime_counterfactual.txt"

# Closed paper trades from VPS ledger snapshot (paper_loss_analysis.txt).
CLOSED_TRADES = [
    {"id": 1600, "symbol": "WUSDT", "direction": "SHORT", "score": 21.1, "pnl": -28.03, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-07-31T18:00:00+00:00"},
    {"id": 1602, "symbol": "IMXUSDT", "direction": "STRONG_SHORT", "score": 19.7, "pnl": -24.70, "r": -1.07, "exit": "stop_loss", "opened_at": "2026-07-31T18:00:00+00:00"},
    {"id": 1608, "symbol": "NESUSDT", "direction": "SHORT", "score": 24.7, "pnl": -21.01, "r": -1.08, "exit": "stop_loss", "opened_at": "2026-08-01T10:00:00+00:00"},
    {"id": 1601, "symbol": "ATOMUSDT", "direction": "SHORT", "score": 24.8, "pnl": 16.31, "r": 0.82, "exit": "expired", "opened_at": "2026-07-31T17:00:00+00:00"},
    {"id": 1605, "symbol": "VANAUSDT", "direction": "SHORT", "score": 25.0, "pnl": 18.65, "r": 0.57, "exit": "expired", "opened_at": "2026-08-01T02:00:00+00:00"},
    {"id": 1619, "symbol": "APTUSDT", "direction": "SHORT", "score": 25.0, "pnl": -18.59, "r": -1.09, "exit": "stop_loss", "opened_at": "2026-08-02T00:00:00+00:00"},
    {"id": 1614, "symbol": "SOLUSDT", "direction": "SHORT", "score": 24.3, "pnl": -15.58, "r": -1.11, "exit": "stop_loss", "opened_at": "2026-08-01T23:00:00+00:00"},
    {"id": 1609, "symbol": "SKYUSDT", "direction": "STRONG_SHORT", "score": 19.9, "pnl": -24.71, "r": -1.07, "exit": "stop_loss", "opened_at": "2026-08-01T12:00:00+00:00"},
    {"id": 1615, "symbol": "WAVESUSDT", "direction": "SHORT", "score": 24.1, "pnl": -25.50, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-08-02T00:00:00+00:00"},
    {"id": 1616, "symbol": "SKRUSDT", "direction": "SHORT", "score": 24.6, "pnl": -18.80, "r": -1.09, "exit": "stop_loss", "opened_at": "2026-08-02T02:00:00+00:00"},
    {"id": 1613, "symbol": "OPUSDT", "direction": "STRONG_SHORT", "score": 19.2, "pnl": -21.28, "r": -1.08, "exit": "stop_loss", "opened_at": "2026-08-02T01:00:00+00:00"},
    {"id": 1623, "symbol": "WIFUSDT", "direction": "SHORT", "score": 24.6, "pnl": -25.99, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-08-02T03:00:00+00:00"},
]

TFS = ("1h", "4h", "1d", "1w")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _fetch_klines(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    with httpx.Client(timeout=30.0) as client:
        while cursor < end_ms:
            resp = client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + 1
            if len(batch) < 1000:
                break
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
    df = pd.DataFrame(
        {
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [float(r[5]) for r in rows],
        },
        index=idx,
    )
    return df[~df.index.duplicated(keep="last")].sort_index()


@dataclass
class TradeImpact:
    id: int
    symbol: str
    direction: str
    coin_score: float
    blended_score: float
    score_delta: float
    bias: str
    btc_score: float
    veto: bool
    veto_reason: str | None
    pnl: float
    r: float
    exit: str
    opened_at: str
    kept_under_veto: bool


def main() -> int:
    opens = [_parse_ts(t["opened_at"]) for t in CLOSED_TRADES]
    start = min(opens) - timedelta(days=220)
    end = max(opens) + timedelta(hours=4)

    print(f"Fetching BTCUSDT candles {start.date()} -> {end.date()} ...")
    frames = {tf: _fetch_klines("BTCUSDT", tf, start, end) for tf in TFS}
    for tf, df in frames.items():
        print(f"  {tf}: {len(df)} bars")

    engine = MarketRegimeEngine()
    calc = FinalScoreCalculator(
        ScoreWeights(coin=0.60, global_market=0.25, funding=0.0, open_interest=0.0, liquidations=0.0)
    )

    impacts: list[TradeImpact] = []
    for trade in CLOSED_TRADES:
        cutoff = _parse_ts(trade["opened_at"])
        sliced = {
            tf: df.loc[df.index <= cutoff]
            for tf, df in frames.items()
            if len(df.loc[df.index <= cutoff]) >= 50
        }
        snap = engine.resolve_from_btc_frames(sliced)
        direction = SignalDirection(trade["direction"])
        veto_reason = hard_veto_reason(snap, direction, enabled=True)
        blend = calc.blend(float(trade["score"]), direction, snap)
        impacts.append(
            TradeImpact(
                id=int(trade["id"]),
                symbol=str(trade["symbol"]),
                direction=str(trade["direction"]),
                coin_score=float(trade["score"]),
                blended_score=blend.final_score,
                score_delta=round(blend.final_score - float(trade["score"]), 2),
                bias=snap.bias.value if snap.available else "unavailable",
                btc_score=snap.btc.score if snap.btc.available else 0.0,
                veto=veto_reason is not None,
                veto_reason=veto_reason,
                pnl=float(trade["pnl"]),
                r=float(trade["r"]),
                exit=str(trade["exit"]),
                opened_at=str(trade["opened_at"]),
                kept_under_veto=veto_reason is None,
            )
        )

    baseline_pnl = sum(t.pnl for t in impacts)
    vetoed = [t for t in impacts if t.veto]
    kept = [t for t in impacts if not t.veto]
    pnl_if_veto = sum(t.pnl for t in kept)
    pnl_removed = sum(t.pnl for t in vetoed)

    # Soft filter A: current blend + short_max=25 (high blended score rejects short)
    soft_max = 25.0
    soft_blocked = [t for t in impacts if t.direction.endswith("SHORT") and t.blended_score > soft_max]
    soft_or_veto_kept = [t for t in impacts if (not t.veto) and t not in soft_blocked]
    pnl_soft = sum(t.pnl for t in soft_or_veto_kept)

    # Soft filter B: short-aware remap — aligned bearish market should LOWER short scores.
    # final' = 100 - (w_coin*(100-coin) + w_mkt*side_for_short)
    # where side_for_short = 50 + 0.5*(-global)  [bearish global → high side → low final]
    alt_rows: list[dict] = []
    for t in impacts:
        # Recover global from blend approx via engine snap already in t.btc_score as proxy
        # Recompute properly from stored bias score field.
        pass
    # Recompute alt blend from saved btc_score (global≈btc in backtest path)
    alt_blocked: list[TradeImpact] = []
    alt_kept_pnl = 0.0
    for t in impacts:
        global_raw = t.btc_score  # resolve_from_btc_frames uses BTC-heavy global
        # Quality of short in 0..100 (higher=better short)
        coin_short_q = 100.0 - t.coin_score
        mkt_short_q = max(0.0, min(100.0, 50.0 + 0.5 * (-global_raw)))
        blended_q = 0.60 * coin_short_q + 0.40 * mkt_short_q
        alt_score = round(100.0 - blended_q, 2)
        # Keep short if still <= short_max after remap
        blocked = alt_score > soft_max
        if blocked:
            alt_blocked.append(t)
        else:
            alt_kept_pnl += t.pnl
        alt_rows.append(
            {
                "id": t.id,
                "symbol": t.symbol,
                "alt_score": alt_score,
                "blocked": blocked,
                "pnl": t.pnl,
            }
        )

    summary = {
        "n_closed": len(impacts),
        "baseline_pnl": round(baseline_pnl, 2),
        "hard_veto_blocked_n": len(vetoed),
        "hard_veto_blocked_pnl": round(pnl_removed, 2),
        "pnl_after_hard_veto": round(pnl_if_veto, 2),
        "hard_veto_delta": round(pnl_if_veto - baseline_pnl, 2),
        "soft_blend_blocked_n": len(soft_blocked),
        "pnl_after_hard_veto_and_soft_max25": round(pnl_soft, 2),
        "soft_combo_delta": round(pnl_soft - baseline_pnl, 2),
        "alt_short_remap_blocked_n": len(alt_blocked),
        "pnl_after_alt_short_remap": round(alt_kept_pnl, 2),
        "alt_short_remap_delta": round(alt_kept_pnl - baseline_pnl, 2),
        "alt_rows": alt_rows,
        "note": (
            "Current FinalScoreCalculator raises short scores in bearish regimes; "
            "with SIGNAL_SHORT_MAX_SCORE=25 that rejects almost all shorts. "
            "alt_short_remap inverts short scale so aligned bearish markets strengthen shorts."
        ),
        "bias_counts": {},
        "trades": [asdict(t) for t in impacts],
    }
    for t in impacts:
        summary["bias_counts"][t.bias] = summary["bias_counts"].get(t.bias, 0) + 1

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "PAPER REGIME COUNTERFACTUAL",
        f"Closed trades: {len(impacts)} (all SHORT)",
        f"Baseline realized PnL: {baseline_pnl:+.2f}",
        "",
        f"Hard veto blocked: {len(vetoed)} trades, PnL removed {pnl_removed:+.2f}",
        f"PnL if hard veto applied: {pnl_if_veto:+.2f}  (delta {pnl_if_veto - baseline_pnl:+.2f})",
        f"Soft blend CURRENT (score>25 drop): {pnl_soft:+.2f}  (delta {pnl_soft - baseline_pnl:+.2f})",
        f"Soft blend ALT short-remap (<=25 keep): {alt_kept_pnl:+.2f}  (delta {alt_kept_pnl - baseline_pnl:+.2f})",
        "",
        "NOTE: Short blend uses inverted engine scale (low score = strong short).",
        "",
        "Bias at entry:",
    ]
    for bias, n in sorted(summary["bias_counts"].items()):
        lines.append(f"  {bias}: {n}")
    lines.append("")
    lines.append(
        f"{'ID':>4} {'SYM':10} {'COIN':>5} {'BLEND':>5} {'Δ':>5} {'BIAS':16} {'VETO':5} {'PNL':>7} {'EXIT'}"
    )
    for t in impacts:
        lines.append(
            f"{t.id:4d} {t.symbol:10} {t.coin_score:5.1f} {t.blended_score:5.1f} "
            f"{t.score_delta:+5.1f} {t.bias:16} {'YES' if t.veto else 'no':5} "
            f"{t.pnl:+7.2f} {t.exit}"
        )
    text = "\n".join(lines) + "\n"
    OUT_TXT.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
