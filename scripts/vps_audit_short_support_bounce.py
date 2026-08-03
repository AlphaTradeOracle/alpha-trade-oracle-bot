"""Audit: would a 'no short into support bounce' gate have helped?

Flags (MOCA lesson):
  1. LTF bullish divergence (counter_arguments / reasons)
  2. RSI rising while short (momentum detail)
  3. Structure RANGE + near support (market_context.intelligence.structure)
  4. Breakdown without volume confirmation
  5. Very low ATR / little movement (volatility detail)

Usage (VPS worker):
  python scripts/vps_audit_short_support_bounce.py --since 2026-07-31T16:32:35+00:00
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.logging import configure_logging
from app.core.time import ensure_utc
from app.database.session import session_scope
from app.models.paper import PaperPosition
from app.models.signal import Signal, SignalScoreComponent


def _parse_since(raw: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))


def _text_blob(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, list):
            chunks.extend(str(x) for x in part)
        elif isinstance(part, dict):
            chunks.append(json.dumps(part, default=str))
        else:
            chunks.append(str(part))
    return " | ".join(chunks).lower()


def _flags_for_signal(signal: Signal | None) -> dict[str, Any]:
    if signal is None:
        return {
            "bullish_div": False,
            "rsi_rising": False,
            "range_near_support": False,
            "breakdown_no_volume": False,
            "low_atr": False,
            "structure": None,
            "hits": [],
        }

    reasons = signal.reasons or []
    counters = signal.counter_arguments or []
    mctx = signal.market_context or {}
    intel = mctx.get("intelligence") if isinstance(mctx, dict) else {}
    structure = (intel or {}).get("structure") if isinstance(intel, dict) else {}
    struct_label = (structure or {}).get("structure")
    struct_reasons = " ".join(str(x) for x in ((structure or {}).get("reasons") or []))

    comps = list(signal.score_components or [])
    mom = next((c for c in comps if c.category == "momentum"), None)
    vol = next((c for c in comps if c.category == "volume"), None)
    atr = next((c for c in comps if c.category == "volatility"), None)

    blob = _text_blob(reasons, counters, struct_reasons, getattr(mom, "detail", None), getattr(vol, "detail", None), getattr(atr, "detail", None))

    bullish_div = "bullish divergence" in blob
    rsi_rising = bool(re.search(r"rsi rising", blob))
    near_support = "ueber einem support" in blob or "über einem support" in blob or "near support" in blob or "unmittelbar ueber einem support" in blob or "unmittelbar über einem support" in blob
    range_struct = str(struct_label or "").upper() == "RANGE" or "seitwaert" in blob or "range" in str(struct_label or "").lower()
    range_near_support = range_struct and near_support
    breakdown_no_volume = "without volume confirmation" in blob or "ohne volume" in blob
    low_atr = "very low" in blob and "atr" in blob

    hits: list[str] = []
    if bullish_div:
        hits.append("bullish_div")
    if rsi_rising:
        hits.append("rsi_rising")
    if range_near_support:
        hits.append("range_near_support")
    if breakdown_no_volume:
        hits.append("breakdown_no_volume")
    if low_atr:
        hits.append("low_atr")

    return {
        "bullish_div": bullish_div,
        "rsi_rising": rsi_rising,
        "range_near_support": range_near_support,
        "breakdown_no_volume": breakdown_no_volume,
        "low_atr": low_atr,
        "structure": struct_label,
        "hits": hits,
    }


def _gate_variants(hits: list[str]) -> dict[str, bool]:
    """Proposed rule stacks — stricter to looser."""
    s = set(hits)
    return {
        # A: hard MOCA pattern — bounce cues
        "A_div_or_range_support": bool(s & {"bullish_div", "range_near_support"}),
        # B: bounce + weak continuation
        "B_bounce_and_weak_cont": (
            bool(s & {"bullish_div", "range_near_support", "rsi_rising"})
            and bool(s & {"breakdown_no_volume", "low_atr", "rsi_rising"})
        ),
        # C: any 2 of the 5 flags
        "C_any_2_flags": len(s) >= 2,
        # D: divergence alone (aggressive)
        "D_div_only": "bullish_div" in s,
        # E: range+support OR (div AND rsi_rising)
        "E_support_or_div_rising": (
            "range_near_support" in s or ("bullish_div" in s and "rsi_rising" in s)
        ),
    }


async def _run(since: datetime) -> dict[str, Any]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(PaperPosition)
                .where(
                    PaperPosition.direction.in_(("SHORT", "STRONG_SHORT")),
                    PaperPosition.status == "closed",
                    PaperPosition.opened_at >= since,
                )
                .order_by(PaperPosition.opened_at.asc())
            )
        ).scalars().all()

        signal_ids = [p.signal_id for p in rows if p.signal_id is not None]
        signals: dict[int, Signal] = {}
        if signal_ids:
            sig_rows = (
                await session.execute(
                    select(Signal)
                    .options(selectinload(Signal.score_components))
                    .where(Signal.id.in_(signal_ids))
                )
            ).scalars().all()
            signals = {s.id: s for s in sig_rows}

    trades: list[dict[str, Any]] = []
    for p in rows:
        sig = signals.get(p.signal_id) if p.signal_id else None
        flags = _flags_for_signal(sig)
        gates = _gate_variants(flags["hits"])
        pnl = float(p.realized_pnl or 0.0)
        risk = float(p.risk_amount or 0.0)
        r_mult = (pnl / risk) if risk > 1e-12 else 0.0
        trades.append(
            {
                "id": p.id,
                "symbol": p.symbol,
                "score": float(p.signal_score or 0.0),
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                "exit_reason": p.exit_reason,
                "pnl": round(pnl, 2),
                "r": round(r_mult, 3),
                "win": pnl > 0,
                "signal_id": p.signal_id,
                "structure": flags["structure"],
                "hits": flags["hits"],
                "gates": gates,
            }
        )

    def _summarize(key: str) -> dict[str, Any]:
        blocked = [t for t in trades if t["gates"].get(key)]
        kept = [t for t in trades if not t["gates"].get(key)]
        blocked_pnl = sum(t["pnl"] for t in blocked)
        kept_pnl = sum(t["pnl"] for t in kept)
        base_pnl = sum(t["pnl"] for t in trades)
        avoided_losses = sum(t["pnl"] for t in blocked if t["pnl"] < 0)
        missed_wins = sum(t["pnl"] for t in blocked if t["pnl"] > 0)
        return {
            "blocked_n": len(blocked),
            "kept_n": len(kept),
            "blocked_pnl": round(blocked_pnl, 2),
            "kept_pnl": round(kept_pnl, 2),
            "delta_vs_base": round(kept_pnl - base_pnl, 2),
            # Blocking a -27 trade improves book by +27 → report book_delta = -blocked_pnl
            "book_if_blocked": round(base_pnl - blocked_pnl, 2),
            "improvement": round(-blocked_pnl, 2),
            "avoided_loss_sum": round(abs(avoided_losses), 2) if avoided_losses < 0 else 0.0,
            "missed_win_sum": round(missed_wins, 2),
            "blocked_wr": round(
                sum(1 for t in blocked if t["win"]) / len(blocked) * 100, 1
            )
            if blocked
            else None,
            "kept_wr": round(sum(1 for t in kept if t["win"]) / len(kept) * 100, 1)
            if kept
            else None,
            "blocked_examples": [
                {
                    "symbol": t["symbol"],
                    "pnl": t["pnl"],
                    "exit": t["exit_reason"],
                    "hits": t["hits"],
                }
                for t in sorted(blocked, key=lambda x: x["pnl"])[:8]
            ],
            "false_blocks": [
                {
                    "symbol": t["symbol"],
                    "pnl": t["pnl"],
                    "exit": t["exit_reason"],
                    "hits": t["hits"],
                }
                for t in sorted(blocked, key=lambda x: -x["pnl"])[:8]
                if t["pnl"] > 0
            ],
        }

    base_pnl = sum(t["pnl"] for t in trades)
    base_wr = (
        sum(1 for t in trades if t["win"]) / len(trades) * 100.0 if trades else 0.0
    )
    flag_counts = {
        k: sum(1 for t in trades if k in t["hits"])
        for k in (
            "bullish_div",
            "rsi_rising",
            "range_near_support",
            "breakdown_no_volume",
            "low_atr",
        )
    }

    variants = {
        key: _summarize(key)
        for key in (
            "A_div_or_range_support",
            "B_bounce_and_weak_cont",
            "C_any_2_flags",
            "D_div_only",
            "E_support_or_div_rising",
        )
    }

    # Recommend best improvement with not too many false blocks
    ranked = sorted(
        variants.items(),
        key=lambda kv: (
            -kv[1]["improvement"],
            kv[1]["missed_win_sum"],
            kv[1]["blocked_n"],
        ),
    )

    moca = [t for t in trades if t["symbol"] == "MOCAUSDT"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since.isoformat(),
        "n_closed_shorts": len(trades),
        "base_pnl": round(base_pnl, 2),
        "base_wr": round(base_wr, 1),
        "flag_counts": flag_counts,
        "variants": variants,
        "recommended": ranked[0][0] if ranked else None,
        "moca": moca,
        "trades": trades,
    }


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default="2026-07-31T16:32:35+00:00",
        help="Paper reset cutoff (UTC)",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    import asyncio

    payload = asyncio.run(_run(_parse_since(args.since)))
    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
