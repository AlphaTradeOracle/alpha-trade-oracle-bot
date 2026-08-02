"""Counterfactual backtest: paper trades since reset under Institutional KB.

Applies at each closed-trade open time:
  1. Market Regime (BTC MTF) + hard veto
  2. Soft score blend (short-aware)
  3. Institutional Market Intelligence (phase / narrative / structure / DQ)
  4. Probability + no-trade gates (confidence / EV / regime / liquidity)

Usage:
  python scripts/analyze_paper_institutional_backtest.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings
from app.core.enums import Confidence, MarketPhase, SignalDirection
from app.intelligence.orchestrator import InstitutionalIntelligenceOrchestrator
from app.intelligence.types import InstitutionalContext
from app.knowledge.no_trade import NoTradeContext, evaluate_no_trade_gates
from app.market_regime import MarketRegimeEngine, hard_veto_reason
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.types import MarketRegimeSnapshot, ScoreWeights
from app.signals.types import RiskParameters, SignalResult

BINANCE = "https://api.binance.com"
OUT_JSON = ROOT / "exports" / "paper_institutional_backtest.json"
OUT_TXT = ROOT / "exports" / "paper_institutional_backtest.txt"
LEDGER = ROOT / "exports" / "paper_since_reset.txt"

# Fallback if VPS export missing (from paper_loss_analysis snapshot).
FALLBACK_CLOSED = [
    {"id": 1600, "symbol": "WUSDT", "direction": "SHORT", "score": 21.1, "pnl": -28.03, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-07-31T18:00:00+00:00", "risk": 26.52},
    {"id": 1602, "symbol": "IMXUSDT", "direction": "STRONG_SHORT", "score": 19.7, "pnl": -24.70, "r": -1.07, "exit": "stop_loss", "opened_at": "2026-07-31T18:00:00+00:00", "risk": 23.19},
    {"id": 1608, "symbol": "NESUSDT", "direction": "SHORT", "score": 24.7, "pnl": -21.01, "r": -1.08, "exit": "stop_loss", "opened_at": "2026-08-01T10:00:00+00:00", "risk": 19.50},
    {"id": 1601, "symbol": "ATOMUSDT", "direction": "SHORT", "score": 24.8, "pnl": 16.31, "r": 0.82, "exit": "expired", "opened_at": "2026-07-31T17:00:00+00:00", "risk": 19.82},
    {"id": 1605, "symbol": "VANAUSDT", "direction": "SHORT", "score": 25.0, "pnl": 18.65, "r": 0.57, "exit": "expired", "opened_at": "2026-08-01T02:00:00+00:00", "risk": 32.92},
    {"id": 1619, "symbol": "APTUSDT", "direction": "SHORT", "score": 25.0, "pnl": -18.59, "r": -1.09, "exit": "stop_loss", "opened_at": "2026-08-02T00:00:00+00:00", "risk": 17.08},
    {"id": 1614, "symbol": "SOLUSDT", "direction": "SHORT", "score": 24.3, "pnl": -15.58, "r": -1.11, "exit": "stop_loss", "opened_at": "2026-08-01T23:00:00+00:00", "risk": 14.07},
    {"id": 1609, "symbol": "SKYUSDT", "direction": "STRONG_SHORT", "score": 19.9, "pnl": -24.71, "r": -1.07, "exit": "stop_loss", "opened_at": "2026-08-01T12:00:00+00:00", "risk": 23.20},
    {"id": 1615, "symbol": "WAVESUSDT", "direction": "SHORT", "score": 24.1, "pnl": -25.50, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-08-02T00:00:00+00:00", "risk": 23.98},
    {"id": 1616, "symbol": "SKRUSDT", "direction": "SHORT", "score": 24.6, "pnl": -18.80, "r": -1.09, "exit": "stop_loss", "opened_at": "2026-08-02T02:00:00+00:00", "risk": 17.29},
    {"id": 1613, "symbol": "OPUSDT", "direction": "STRONG_SHORT", "score": 19.2, "pnl": -21.28, "r": -1.08, "exit": "stop_loss", "opened_at": "2026-08-02T01:00:00+00:00", "risk": 19.77},
    {"id": 1623, "symbol": "WIFUSDT", "direction": "SHORT", "score": 24.6, "pnl": -25.99, "r": -1.06, "exit": "stop_loss", "opened_at": "2026-08-02T03:00:00+00:00", "risk": 24.48},
]

TFS = ("1h", "4h", "1d", "1w")
SHORT_MAX = 25.0


def _parse_ts(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00").replace(" ", "T")
    # Postgres often emits +00 without minutes.
    if raw.endswith("+00"):
        raw = raw + ":00"
    elif raw.endswith("-00"):
        raw = raw[:-3] + "+00:00"
    return datetime.fromisoformat(raw).astimezone(UTC)


def _load_closed() -> list[dict]:
    if not LEDGER.exists():
        return list(FALLBACK_CLOSED)
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    mode = None
    rows: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line in {"META", "CLOSED", "OPEN"}:
            mode = line
            continue
        if mode != "CLOSED":
            continue
        parts = line.split("|")
        if len(parts) < 9:
            continue
        r_raw = parts[5]
        rows.append(
            {
                "id": int(parts[0]),
                "symbol": parts[1],
                "direction": parts[2],
                "score": float(parts[3]),
                "pnl": float(parts[4]),
                "r": None if r_raw in {"", "null"} else float(r_raw),
                "exit": parts[6],
                "opened_at": parts[7],
                "risk": float(parts[11]) if len(parts) > 11 and parts[11] else 0.0,
            }
        )
    return rows or list(FALLBACK_CLOSED)


def _fetch_klines(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    with httpx.Client(timeout=45.0) as client:
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


def _fake_signal(trade: dict, blended: float) -> SignalResult:
    direction = SignalDirection(trade["direction"])
    # Heuristic categorical confidence from setup quality.
    quality = blended if direction.is_long else 100.0 - blended
    if quality >= 85:
        conf = Confidence.HIGH
    elif quality >= 70:
        conf = Confidence.MEDIUM
    else:
        conf = Confidence.LOW
    risk_amt = float(trade.get("risk") or 20.0)
    rr = 2.0
    return SignalResult(
        symbol=str(trade["symbol"]),
        created_at=_parse_ts(trade["opened_at"]),
        expires_at=_parse_ts(trade["opened_at"]) + timedelta(hours=24),
        direction=direction,
        score=float(blended),
        confidence=conf,
        market_phase=MarketPhase.DOWNTREND if direction.is_short else MarketPhase.UPTREND,
        primary_timeframe="4h",
        analyzed_timeframes=["1h", "4h", "1d"],
        reference_price=1.0,
        data_quality=88.0,
        components=[],
        assessments={},
        risk=RiskParameters(
            entry_low=1.0,
            entry_high=1.0,
            stop_loss=0.95,
            take_profit_1=1.1,
            take_profit_2=1.2,
            take_profit_3=1.3,
            risk_reward_ratio=rr,
            risk_percent=1.0,
            suggested_position_size=risk_amt,
            stop_distance_percent=2.0,
            invalidation_note="backtest",
        ),
        reasons=["paper counterfactual"],
        coin_score=float(trade["score"]),
    )


@dataclass
class TradeRow:
    id: int
    symbol: str
    direction: str
    coin_score: float
    blended_score: float
    bias: str
    phase: str
    narrative: str
    structure: str
    confidence_pct: float
    expected_value: float | None
    decision: str
    hard_veto: bool
    soft_max_block: bool
    institutional_block: bool
    keep_institutional: bool
    keep_soft_only: bool
    gates: list[str] = field(default_factory=list)
    pnl: float = 0.0
    r: float | None = None
    exit: str = ""
    opened_at: str = ""
    summary: str = ""


def main() -> int:
    trades = _load_closed()
    opens = [_parse_ts(t["opened_at"]) for t in trades]
    start = min(opens) - timedelta(days=220)
    end = max(opens) + timedelta(hours=4)

    print(f"Closed trades: {len(trades)}")
    print(f"Fetching BTCUSDT {start.date()} -> {end.date()} ...")
    frames = {tf: _fetch_klines("BTCUSDT", tf, start, end) for tf in TFS}
    for tf, df in frames.items():
        print(f"  {tf}: {len(df)} bars")

    settings = Settings(
        app_env="development",
        institutional_kb_enabled=True,
        institutional_enforce_gates=True,
        institutional_min_confidence_pct=55.0,
        institutional_min_data_quality=70.0,
        institutional_require_positive_ev=False,
        institutional_reject_thin_liquidity=False,
        market_regime_hard_veto=True,
        signal_min_score=75.0,
        min_risk_reward_ratio=1.5,
    )
    orch = InstitutionalIntelligenceOrchestrator(settings)
    engine = MarketRegimeEngine(settings)
    calc = FinalScoreCalculator(
        ScoreWeights(coin=0.60, global_market=0.25, funding=0.0, open_interest=0.0, liquidations=0.0)
    )

    rows: list[TradeRow] = []
    for trade in trades:
        cutoff = _parse_ts(trade["opened_at"])
        sliced = {
            tf: df.loc[df.index <= cutoff]
            for tf, df in frames.items()
            if len(df.loc[df.index <= cutoff]) >= 50
        }
        snap: MarketRegimeSnapshot = engine.resolve_from_btc_frames(sliced)
        direction = SignalDirection(trade["direction"])
        veto = hard_veto_reason(snap, direction, enabled=True)
        blend = calc.blend(float(trade["score"]), direction, snap)

        intel: InstitutionalContext = orch.build_market_intelligence(
            snap, candle_data_quality=88.0, exchange_ok=True
        )
        signal = _fake_signal(trade, blend.final_score)
        explain = orch.finalize_trade(signal, intel)

        soft_block = direction.is_short and blend.final_score > SHORT_MAX
        inst_block = bool(explain.no_trade_gates) or signal.direction is SignalDirection.NO_TRADE
        hard = veto is not None
        keep_soft = (not hard) and (not soft_block)
        keep_inst = (not hard) and (not soft_block) and (not inst_block)

        rows.append(
            TradeRow(
                id=int(trade["id"]),
                symbol=str(trade["symbol"]),
                direction=str(trade["direction"]),
                coin_score=float(trade["score"]),
                blended_score=round(blend.final_score, 2),
                bias=snap.bias.value if snap.available else "unavailable",
                phase=intel.phase.phase.value if intel.phase else "unknown",
                narrative=intel.narrative.primary.value if intel.narrative else "unknown",
                structure=intel.structure.structure_label if intel.structure else "unknown",
                confidence_pct=round(explain.confidence_pct, 1),
                expected_value=explain.expected_value,
                decision=explain.decision.value,
                hard_veto=hard,
                soft_max_block=soft_block,
                institutional_block=inst_block,
                keep_institutional=keep_inst,
                keep_soft_only=keep_soft,
                gates=list(explain.no_trade_gates),
                pnl=float(trade["pnl"]),
                r=trade.get("r"),
                exit=str(trade["exit"]),
                opened_at=str(trade["opened_at"]),
                summary=explain.natural_language[:180],
            )
        )

    baseline = sum(r.pnl for r in rows)
    soft_kept = [r for r in rows if r.keep_soft_only]
    inst_kept = [r for r in rows if r.keep_institutional]
    pnl_soft = sum(r.pnl for r in soft_kept)
    pnl_inst = sum(r.pnl for r in inst_kept)

    # Gate frequency
    gate_counts: dict[str, int] = {}
    for r in rows:
        for g in r.gates:
            gate_counts[g] = gate_counts.get(g, 0) + 1

    summary = {
        "type": "paper_institutional_kb_backtest",
        "as_of": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "n_closed": len(rows),
        "baseline_pnl": round(baseline, 2),
        "soft_blend_kept_n": len(soft_kept),
        "pnl_after_soft_blend_max25": round(pnl_soft, 2),
        "soft_delta": round(pnl_soft - baseline, 2),
        "institutional_kept_n": len(inst_kept),
        "pnl_after_institutional": round(pnl_inst, 2),
        "institutional_delta": round(pnl_inst - baseline, 2),
        "hard_veto_n": sum(1 for r in rows if r.hard_veto),
        "soft_max_block_n": sum(1 for r in rows if r.soft_max_block),
        "institutional_block_n": sum(1 for r in rows if r.institutional_block),
        "gate_counts": gate_counts,
        "skipped_institutional": [
            {
                "id": r.id,
                "symbol": r.symbol,
                "pnl": r.pnl,
                "gates": r.gates,
                "soft_max_block": r.soft_max_block,
                "confidence_pct": r.confidence_pct,
                "phase": r.phase,
                "narrative": r.narrative,
            }
            for r in rows
            if not r.keep_institutional
        ],
        "kept_institutional": [
            {"id": r.id, "symbol": r.symbol, "pnl": r.pnl, "decision": r.decision}
            for r in inst_kept
        ],
        "trades": [asdict(r) for r in rows],
        "config": {
            "short_max": SHORT_MAX,
            "min_confidence_pct": settings.institutional_min_confidence_pct,
            "min_data_quality": settings.institutional_min_data_quality,
            "require_positive_ev": settings.institutional_require_positive_ev,
            "enforce_gates": settings.institutional_enforce_gates,
        },
        "note": (
            "Counterfactual on realized closed paper trades since reset. "
            "Coin scores are historical; market intel rebuilt from BTC MTF at entry. "
            "Aux feeds (funding/OI/F&G) unavailable in offline path → slightly lower confidence."
        ),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "PAPER INSTITUTIONAL KB BACKTEST (since reset)",
        f"Closed trades: {len(rows)}",
        f"Baseline realized PnL: {baseline:+.2f}",
        "",
        f"Soft blend + short_max={SHORT_MAX}: kept {len(soft_kept)} → PnL {pnl_soft:+.2f} (Δ {pnl_soft - baseline:+.2f})",
        f"Institutional KB full stack: kept {len(inst_kept)} → PnL {pnl_inst:+.2f} (Δ {pnl_inst - baseline:+.2f})",
        f"Hard veto blocks: {summary['hard_veto_n']}",
        f"Soft max blocks: {summary['soft_max_block_n']}",
        f"Institutional gate blocks: {summary['institutional_block_n']}",
        "",
        "Gate counts:",
    ]
    for g, n in sorted(gate_counts.items(), key=lambda x: -x[1]):
        lines.append(f"  {g}: {n}")
    lines += [
        "",
        f"{'ID':>4} {'SYM':10} {'COIN':>5} {'BLEND':>5} {'CONF':>5} {'PHASE':18} {'KEEP':4} {'PNL':>8} {'GATES'}",
    ]
    for r in rows:
        keep = "yes" if r.keep_institutional else "SKIP"
        gates = ",".join(r.gates[:2]) if r.gates else ("soft_max" if r.soft_max_block else "-")
        lines.append(
            f"{r.id:4d} {r.symbol:10} {r.coin_score:5.1f} {r.blended_score:5.1f} "
            f"{r.confidence_pct:5.1f} {r.phase:18} {keep:4} {r.pnl:+8.2f} {gates}"
        )
    text = "\n".join(lines) + "\n"
    OUT_TXT.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
