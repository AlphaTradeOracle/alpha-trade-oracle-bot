"""Apply combo_active gates live and rebuild default paper since start.

1) Persistently revive stored NO_TRADE rows that pass the new gates
   (same rules as the equity-sweep sim).
2) Rebuild paper account ``default`` with current settings (env-driven).

Does not touch Telegram dispatch. Safe to re-run: already-actionable rows
stay untouched; previously revived rows keep STRONG direction.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.signal import Signal
from app.repositories.paper_repository import PaperRepository

_ADX_RE = re.compile(r"ADX\s+(\d+(?:\.\d+)?)", re.I)
_RSI_RE = re.compile(r"RSI\s+(\d+(?:\.\d+)?)", re.I)
_REVIVE_NOTE = "revived_combo_active"


def _parse_adx(reason: str | None) -> float | None:
    if not reason:
        return None
    m = _ADX_RE.search(reason)
    return float(m.group(1)) if m else None


def _parse_rsi(reason: str | None) -> float | None:
    if not reason:
        return None
    m = _RSI_RE.search(reason)
    return float(m.group(1)) if m else None


def _infer_direction(
    score: float, *, long_min: float, short_min: float, short_max: float
) -> SignalDirection | None:
    if score >= long_min:
        return SignalDirection.STRONG_LONG
    if short_min < score <= short_max:
        return SignalDirection.STRONG_SHORT
    return None


def _revive_ok(
    signal: Signal,
    *,
    long_min: float,
    short_min: float,
    short_max: float,
    adx_min: float,
    adx_soft: float,
    rsi_short_min: float,
    rr_min: float,
) -> SignalDirection | None:
    reason = (signal.no_trade_reason or "").strip()
    if not reason:
        return None
    if signal.stop_loss is None or signal.take_profit_1 is None:
        return None
    if signal.take_profit_2 is None or signal.take_profit_3 is None:
        return None
    if float(signal.data_quality or 0) < 60.0 and "Data quality" in reason:
        if abs(float(signal.data_quality or 0) - 59.99) > 0.001:
            return None
    rr = float(signal.risk_reward_ratio or 0.0)
    if rr < rr_min:
        return None

    score = float(signal.score)
    direction = _infer_direction(
        score, long_min=long_min, short_min=short_min, short_max=short_max
    )
    if direction is None:
        return None

    hard = (
        "Volatility too high",
        "No reliable risk",
        "exhaustion band",
        "regime",
        "Regime",
    )
    if any(tok in reason for tok in hard):
        return None

    high_conv = (direction.is_long and score >= long_min) or (
        direction.is_short and score <= short_max
    )
    adx_floor = adx_soft if high_conv else adx_min
    adx = _parse_adx(reason)
    rsi = _parse_rsi(reason)

    if "ADX" in reason or "Trend strength" in reason or "Range market" in reason:
        if adx is not None and adx < adx_floor:
            return None
    if direction.is_short and "oversold" in reason.lower():
        if rsi is not None and rsi < rsi_short_min:
            return None
    if direction.is_long and "overbought" in reason.lower():
        if rsi is not None and rsi > 75.0:
            return None
    return direction


async def _snapshot(session) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                select cash_balance, realized_pnl, initial_balance,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='closed') as closed_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='open') as open_n,
                  (select count(*) from paper_positions p
                   where p.account_id=a.id and p.status='pending') as pending_n
                from paper_accounts a where name='default'
                """
            )
        )
    ).mappings().one()
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(row).items()}


async def _revive_signals(
    session,
    *,
    since: datetime,
    dry_run: bool,
) -> dict[str, Any]:
    settings = get_settings()
    long_min = float(settings.signal_min_score)
    short_min = float(settings.signal_short_min_score)
    short_max = float(settings.signal_short_max_score)
    adx_min = float(settings.signal_min_adx)
    adx_soft = float(settings.signal_min_adx_soft)
    rsi_short_min = float(settings.signal_rsi_short_min)
    rr_min = float(settings.min_risk_reward_ratio)

    result = await session.execute(
        select(Signal)
        .where(
            Signal.created_at >= since,
            Signal.direction == SignalDirection.NO_TRADE.value,
            Signal.stop_loss.is_not(None),
            Signal.take_profit_1.is_not(None),
        )
        .order_by(Signal.created_at.asc())
        .limit(50_000)
    )
    candidates: list[tuple[Signal, SignalDirection]] = []
    for signal in result.scalars():
        direction = _revive_ok(
            signal,
            long_min=long_min,
            short_min=short_min,
            short_max=short_max,
            adx_min=adx_min,
            adx_soft=adx_soft,
            rsi_short_min=rsi_short_min,
            rr_min=rr_min,
        )
        if direction is not None:
            candidates.append((signal, direction))

    by_dir = {"STRONG_LONG": 0, "STRONG_SHORT": 0}
    samples: list[dict[str, Any]] = []
    for signal, direction in candidates:
        by_dir[direction.value] = by_dir.get(direction.value, 0) + 1
        if len(samples) < 12:
            samples.append(
                {
                    "id": signal.id,
                    "symbol_asset_id": signal.asset_id,
                    "score": float(signal.score),
                    "to": direction.value,
                    "reason": (signal.no_trade_reason or "")[:80],
                }
            )
        if not dry_run:
            old_reason = signal.no_trade_reason or ""
            signal.direction = direction.value
            signal.no_trade_reason = None
            note = f"{_REVIVE_NOTE}: {old_reason}"[:500]
            if signal.invalidation_note:
                if _REVIVE_NOTE not in signal.invalidation_note:
                    signal.invalidation_note = f"{note} | {signal.invalidation_note}"[:500]
            else:
                signal.invalidation_note = note

    return {
        "revived": len(candidates),
        "by_direction": by_dir,
        "samples": samples,
        "gates": {
            "long_min": long_min,
            "short_band": [short_min, short_max],
            "adx_min": adx_min,
            "adx_soft": adx_soft,
            "rsi_short_min": rsi_short_min,
            "rr_min": rr_min,
            "zone_near": float(settings.paper_retest_zone_near),
            "pending_mult": int(settings.paper_retest_pending_multiplier),
            "retest_enabled": bool(settings.paper_retest_entry_enabled),
        },
    }


async def _rebuild(since: datetime) -> dict[str, Any]:
    settings = get_settings()
    container = build_container(settings)
    paper = container.paper_trading
    provider = container.paper_price_provider

    async with session_scope() as session:
        before = await _snapshot(session)
        with paper._without_notifications():
            result = await paper.rebuild_from_signals(
                session,
                since=since,
                provider=provider,
                providers=None,
                dispatched_only=False,
                one_per_symbol=False,
                symbols=None,
            )
            summary = await paper.summary(session)
            repo = PaperRepository(session)
            account = await repo.get_or_create_account(
                name="default",
                initial_balance=Decimal(str(settings.paper_initial_balance)),
                margin_per_trade=Decimal(str(settings.paper_margin_per_trade)),
                leverage=float(settings.paper_leverage),
            )
            positions = await repo.list_positions(account.id)

    filled = [p for p in positions if p.status in {"open", "closed"}]
    closed = [p for p in positions if p.status == "closed"]
    top = sorted(closed, key=lambda p: float(p.realized_pnl or 0), reverse=True)[:8]
    worst = sorted(closed, key=lambda p: float(p.realized_pnl or 0))[:5]

    def _row(p) -> dict[str, Any]:
        return {
            "symbol": p.symbol,
            "direction": p.direction,
            "status": p.status,
            "score": float(p.signal_score) if p.signal_score is not None else None,
            "pnl": float(p.realized_pnl or 0),
            "exit_reason": p.exit_reason,
            "opened_at": ensure_utc(p.opened_at).isoformat() if p.opened_at else None,
        }

    return {
        "before": before,
        "rebuild": {
            "reset_positions": result.reset_positions,
            "retest_filled": result.retest_filled,
            "retest_skipped": result.retest_skipped,
            "retest_still_pending": result.retest_still_pending,
            "replayed": result.replayed,
            "still_open": result.still_open,
            "opened": result.backfill.opened if result.backfill else 0,
        },
        "after": {
            "equity": float(summary.equity),
            "cash": float(summary.cash_balance),
            "realized_pnl": float(summary.realized_pnl),
            "closed": int(summary.closed_trades),
            "open": int(summary.open_positions),
            "pending": int(summary.pending_positions),
            "win_rate": float(summary.win_rate),
            "profit_factor": float(summary.profit_factor),
            "total_r": float(summary.total_r),
        },
        "filled_symbols": sorted({p.symbol for p in filled}),
        "top_trades": [_row(p) for p in top],
        "worst_trades": [_row(p) for p in worst],
        "at": utc_now().isoformat(),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-05T00:00:00+00:00")
    parser.add_argument("--out", default="/tmp/combo_active_apply.json")
    parser.add_argument("--dry-run-revive", action="store_true")
    parser.add_argument("--skip-rebuild", action="store_true")
    args = parser.parse_args()

    configure_logging("INFO", json_output=False)
    since = ensure_utc(datetime.fromisoformat(args.since))
    settings = get_settings()

    print(
        "gates "
        f"L>={settings.signal_min_score} S={settings.signal_short_min_score}-"
        f"{settings.signal_short_max_score} ADX>={settings.signal_min_adx}/"
        f"{settings.signal_min_adx_soft} RSI>={settings.signal_rsi_short_min} "
        f"zone={settings.paper_retest_zone_near}x{settings.paper_retest_pending_multiplier}",
        flush=True,
    )

    async with session_scope() as session:
        revive = await _revive_signals(
            session, since=since, dry_run=bool(args.dry_run_revive)
        )
    print(
        f"revive n={revive['revived']} by={revive['by_direction']} "
        f"dry={bool(args.dry_run_revive)}",
        flush=True,
    )

    rebuild: dict[str, Any] | None = None
    if not args.skip_rebuild and not args.dry_run_revive:
        print("rebuilding default paper …", flush=True)
        rebuild = await _rebuild(since)
        print(
            f"equity=${rebuild['after']['equity']:.2f} "
            f"realized=${rebuild['after']['realized_pnl']:.2f} "
            f"closed={rebuild['after']['closed']} open={rebuild['after']['open']} "
            f"pending={rebuild['after']['pending']} "
            f"filled={rebuild['rebuild']['retest_filled']} "
            f"WR={rebuild['after']['win_rate']*100:.0f}% "
            f"PF={rebuild['after']['profit_factor']:.2f}",
            flush=True,
        )

    out = {
        "since": since.isoformat(),
        "revive": revive,
        "rebuild": rebuild,
    }
    path = Path(args.out)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"WROTE {path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
