"""Independent verification of paper book since Top400 reset.

Re-simulates allowlist stream with current edge/retest/gates and diffs vs DB.
Also classifies every qualifying allowlist signal: traded / correctly skipped / MISSED.

Run: python /app/scripts/vps_verify_paper_since_reset.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path


SINCE = "2026-07-31T16:32:35+00:00"
ACCOUNT_ID = 1
ALLOWLIST_PATH = Path("/app/scripts/paper_reset_symbols.txt")
# Fill/stop tolerance relative to price
REL_TOL = 1e-4
ABS_TOL_MIN = 1e-10


def _close(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), ABS_TOL_MIN)
    return abs(a - b) <= max(REL_TOL * scale, ABS_TOL_MIN)


def _load_allowlist() -> set[str]:
    out: set[str] = set()
    text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line.upper())
    return out


async def main() -> int:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.container import build_container
    from app.core.config import get_settings
    from app.core.enums import SignalDirection
    from app.core.logging import configure_logging
    from app.core.time import ensure_utc, timeframe_to_timedelta
    from app.database.session import session_scope
    from app.models.paper import PaperAccount, PaperPosition
    from app.repositories.asset_repository import AssetRepository
    from app.repositories.signal_repository import SignalRepository
    from app.signals.retest_entry import (
        RetestEntryConfig,
        arm_retest_entry,
        zone_overlaps_stop,
        retest_zone,
        wilder_atr,
        idx_at_or_before,
        stop_from_retest_fill,
        levels_from_entry_sl,
    )
    from app.signals.risk import tp_multipliers_from_settings

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    container = build_container()
    provider = container.paper_price_provider
    paper = container.paper_trading
    allow = _load_allowlist()
    since = ensure_utc(datetime.fromisoformat(SINCE))
    cutoff = ensure_utc(datetime.now(UTC))
    cfg = RetestEntryConfig(
        zone_near=Decimal(str(settings.paper_retest_zone_near)),
        zone_far=Decimal(str(settings.paper_retest_zone_far)),
        pending_multiplier=int(settings.paper_retest_pending_multiplier),
        min_bars_in_zone=int(settings.paper_retest_min_bars_in_zone),
    )
    lookback = timedelta(days=14)
    tp_mults = tuple(Decimal(str(m)) for m in tp_multipliers_from_settings(settings))

    report: dict = {
        "since": SINCE,
        "cutoff": cutoff.isoformat(),
        "allowlist_n": len(allow),
        "gates": {
            "long_min": settings.signal_min_score,
            "short_max": settings.signal_short_max_score,
            "short_min": settings.signal_short_min_score,
            "retest": settings.paper_retest_entry_enabled,
            "perp": settings.paper_use_perp_prices,
        },
        "account": {},
        "db_book": {},
        "expected_stream": {"filled": [], "skipped": [], "still_pending": []},
        "diffs": {
            "missing_fills": [],
            "extra_fills": [],
            "geometry_mismatch": [],
            "status_mismatch": [],
            "pnl_mismatch": [],
        },
        "signal_audit": {
            "qualifying": 0,
            "correctly_skipped": [],
            "should_have_traded": [],
            "traded_ok": [],
        },
        "verdict": {},
    }

    try:
        async with session_scope() as session:
            account = (
                await session.execute(
                    select(PaperAccount).where(PaperAccount.id == ACCOUNT_ID)
                )
            ).scalar_one()
            cash = float(account.cash_balance)
            realized = float(account.realized_pnl)
            initial = float(account.initial_balance)

            positions = (
                await session.execute(
                    select(PaperPosition)
                    .where(PaperPosition.account_id == ACCOUNT_ID)
                    .order_by(PaperPosition.opened_at.asc())
                )
            ).scalars().all()

            # Positions tied to reset window (opened_at >= since OR closed after)
            book = [
                p
                for p in positions
                if ensure_utc(p.opened_at) >= since
                or (p.closed_at is not None and ensure_utc(p.closed_at) >= since)
            ]
            opens = [p for p in book if p.status == "open"]
            pendings = [p for p in book if p.status == "pending"]
            closed = [p for p in book if p.status == "closed"]
            cancelled = [p for p in book if p.status == "cancelled"]

            open_margin = sum(float(p.margin_used or 0) for p in opens)
            identity_ok = abs((cash + open_margin) - (initial + realized)) < 0.05
            report["account"] = {
                "id": ACCOUNT_ID,
                "cash": cash,
                "realized": realized,
                "initial": initial,
                "open_margin": open_margin,
                "equity_cash_margin": cash + open_margin,
                "identity_ok": identity_ok,
            }
            report["db_book"] = {
                "open": len(opens),
                "pending": len(pendings),
                "closed": len(closed),
                "cancelled": len(cancelled),
                "closed_pnl_sum": sum(float(p.realized_pnl or 0) for p in closed),
                "closed_rows": [
                    {
                        "id": p.id,
                        "signal_id": p.signal_id,
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "entry": float(p.entry_price),
                        "stop": float(p.stop_loss),
                        "pnl": float(p.realized_pnl or 0),
                        "exit": p.exit_reason,
                        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                        "notes": (p.notes or "")[:100],
                    }
                    for p in closed
                ],
            }

            signals = await SignalRepository(session).list_since(
                since, actionable_only=True, dispatched_only=False, limit=5000
            )
            asset_ids = list({s.asset_id for s in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)

            # Chronological expected stream (mirrors rebuild, no portfolio race)
            expected_fills: list[dict] = []
            expected_skips: list[dict] = []
            # symbol -> busy until (closed_at or pending expiry)
            busy_until: dict[str, datetime] = {}
            # When verifying an existing book, prefer real exit times so the
            # stream re-selects the same signal_ids (hold≈expiry alone drifts).
            db_close_by_sig: dict[int, datetime] = {
                int(p.signal_id): ensure_utc(p.closed_at)
                for p in book
                if p.signal_id is not None
                and p.status == "closed"
                and p.closed_at is not None
            }

            candle_cache: dict[tuple[str, str], list] = {}

            async def candles_for(symbol: str, tf: str, start, end):
                key = (symbol, tf)
                if key not in candle_cache:
                    series = await provider.get_candles(
                        symbol, tf, limit=100_000, start_time=start, end_time=end
                    )
                    candle_cache[key] = (
                        list(series.candles) if series and not series.is_empty else []
                    )
                # filter window
                out_c = []
                for c in candle_cache[key]:
                    t = ensure_utc(c.open_time)
                    if t < ensure_utc(start):
                        continue
                    if t > ensure_utc(end):
                        break
                    out_c.append(c)
                # need lookback history for ATR — return full cached series for arm
                return candle_cache[key]

            ordered = sorted(signals, key=lambda s: s.created_at)
            for signal in ordered:
                symbol = symbols_by_id.get(signal.asset_id)
                if not symbol:
                    continue
                symbol = symbol.upper()
                if symbol not in allow:
                    continue
                if not paper._passes_paper_gates(signal):
                    continue

                report["signal_audit"]["qualifying"] += 1
                created = ensure_utc(signal.created_at)
                try:
                    direction = SignalDirection(signal.direction)
                except ValueError:
                    continue
                is_long = direction.is_long

                # edge reference
                if is_long and signal.entry_low is not None:
                    edge = float(signal.entry_low)
                elif (not is_long) and signal.entry_high is not None:
                    edge = float(signal.entry_high)
                else:
                    edge = float(
                        ((float(signal.entry_low or 0) + float(signal.entry_high or 0)) / 2.0)
                        or signal.reference_price
                        or 0
                    )
                stop0 = float(signal.stop_loss) if signal.stop_loss is not None else None
                if edge <= 0 or stop0 is None:
                    expected_skips.append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "bad_levels",
                            "created_at": created.isoformat(),
                        }
                    )
                    continue

                # busy?
                until = busy_until.get(symbol)
                if until is not None and created < until:
                    expected_skips.append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "symbol_busy",
                            "created_at": created.isoformat(),
                            "busy_until": until.isoformat(),
                        }
                    )
                    report["signal_audit"]["correctly_skipped"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "symbol_busy",
                            "score": float(signal.score),
                            "created_at": created.isoformat(),
                        }
                    )
                    continue

                tf = signal.primary_timeframe or "1h"
                pending_until = created + int(cfg.pending_multiplier) * timeframe_to_timedelta(tf)

                try:
                    candles = await candles_for(
                        symbol, tf, created - lookback, cutoff
                    )
                except Exception as exc:
                    expected_skips.append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "no_perp_candles",
                            "error": f"{type(exc).__name__}: {exc}",
                            "created_at": created.isoformat(),
                        }
                    )
                    report["signal_audit"]["correctly_skipped"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "no_perp_candles",
                            "score": float(signal.score),
                            "created_at": created.isoformat(),
                        }
                    )
                    continue

                # zone/stop overlap reject (current arm gate)
                sig_idx = idx_at_or_before(candles, created)
                atr = wilder_atr(candles, sig_idx, cfg.atr_period) if sig_idx is not None else None
                if atr and atr > 0:
                    zlo, zhi = retest_zone(
                        Decimal(str(edge)),
                        Decimal(str(atr)),
                        is_long=is_long,
                        zone_near=cfg.zone_near,
                        zone_far=cfg.zone_far,
                    )
                    if zone_overlaps_stop(zlo, zhi, Decimal(str(stop0))):
                        expected_skips.append(
                            {
                                "signal_id": signal.id,
                                "symbol": symbol,
                                "reason": "zone_stop_overlap",
                                "created_at": created.isoformat(),
                            }
                        )
                        report["signal_audit"]["correctly_skipped"].append(
                            {
                                "signal_id": signal.id,
                                "symbol": symbol,
                                "reason": "zone_stop_overlap",
                                "score": float(signal.score),
                                "created_at": created.isoformat(),
                            }
                        )
                        continue

                arm = arm_retest_entry(
                    direction=direction,
                    arm_time=created,
                    reference_entry=edge,
                    original_stop=stop0,
                    timeframe=tf,
                    candles=candles,
                    config=cfg,
                )

                if arm.filled and arm.fill_price is not None and arm.stop is not None:
                    # verify stop geometry
                    expected_stop = float(
                        stop_from_retest_fill(
                            Decimal(str(arm.fill_price)),
                            reference_entry=Decimal(str(edge)),
                            original_stop=Decimal(str(stop0)),
                            is_long=is_long,
                        )
                    )
                    tp1, tp2, tp3 = levels_from_entry_sl(
                        Decimal(str(arm.fill_price)),
                        Decimal(str(arm.stop)),
                        is_long=is_long,
                        multipliers=tp_mults,
                    )
                    fill_rec = {
                        "signal_id": signal.id,
                        "symbol": symbol,
                        "direction": signal.direction,
                        "score": float(signal.score),
                        "created_at": created.isoformat(),
                        "fill_time": arm.fill_time.isoformat() if arm.fill_time else None,
                        "fill": float(arm.fill_price),
                        "stop": float(arm.stop),
                        "expected_stop": expected_stop,
                        "stop_ok": _close(float(arm.stop), expected_stop),
                        "tp1": float(tp1),
                        "tp2": float(tp2),
                        "tp3": float(tp3),
                        "edge_ref": edge,
                        "orig_sl": stop0,
                        "zone": [arm.zone_lo, arm.zone_hi],
                        "bars_waited": arm.bars_waited,
                    }
                    expected_fills.append(fill_rec)
                    # Mirror rebuild busy window: pending from arm → fill, then
                    # open until real DB close when available, else expiry hold.
                    hold = ensure_utc(arm.fill_time) + int(
                        settings.signal_expiry_multiplier
                    ) * timeframe_to_timedelta(tf)
                    busy_until[symbol] = db_close_by_sig.get(int(signal.id), hold)
                    report["signal_audit"]["traded_ok"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "fill": float(arm.fill_price),
                            "created_at": created.isoformat(),
                        }
                    )
                elif arm.status == "pending" and (
                    pending_until is None or cutoff <= pending_until
                ):
                    report["expected_stream"]["still_pending"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "created_at": created.isoformat(),
                            "expires_at": pending_until.isoformat(),
                        }
                    )
                    busy_until[symbol] = pending_until
                    report["signal_audit"]["correctly_skipped"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": "still_pending",
                            "score": float(signal.score),
                            "created_at": created.isoformat(),
                        }
                    )
                else:
                    reason = arm.status or "skipped"
                    # Critical: skipped arms still occupy the symbol until the
                    # skip-bar / pending expiry (same as paper cancel closed_at).
                    # Without this, later signals inside the pending window are
                    # falsely counted as should_have_traded.
                    free_at = arm.resolved_at or pending_until
                    if free_at is not None:
                        busy_until[symbol] = ensure_utc(free_at)
                    expected_skips.append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": reason,
                            "note": arm.note,
                            "created_at": created.isoformat(),
                            "busy_until": ensure_utc(free_at).isoformat()
                            if free_at is not None
                            else None,
                        }
                    )
                    report["signal_audit"]["correctly_skipped"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "reason": reason,
                            "score": float(signal.score),
                            "created_at": created.isoformat(),
                        }
                    )

            report["expected_stream"]["filled"] = expected_fills
            report["expected_stream"]["skipped"] = expected_skips

            # Diff expected fills vs DB closed + open (filled) for allowlist
            db_filled = [
                p
                for p in book
                if p.status in {"closed", "open"}
                and p.symbol.upper() in allow
                and p.notes
                and "retest_filled" in p.notes
            ]
            # also IST opens without retest note
            db_filled += [
                p
                for p in book
                if p.status in {"closed", "open"}
                and p.symbol.upper() in allow
                and p not in db_filled
                and (not p.notes or "retest_pending" not in (p.notes or ""))
            ]

            by_signal_db: dict[int, PaperPosition] = {}
            for p in book:
                if p.signal_id is not None:
                    by_signal_db.setdefault(int(p.signal_id), p)

            expected_by_sig = {int(x["signal_id"]): x for x in expected_fills}

            for sid, exp in expected_by_sig.items():
                dbp = by_signal_db.get(sid)
                if dbp is None or dbp.status not in {"closed", "open", "pending"}:
                    # pending wouldn't be a fill; missing fill
                    if dbp is None or dbp.status == "cancelled":
                        report["diffs"]["missing_fills"].append(
                            {
                                **exp,
                                "db_status": dbp.status if dbp else None,
                                "db_notes": (dbp.notes or "")[:100] if dbp else None,
                            }
                        )
                        report["signal_audit"]["should_have_traded"].append(
                            {
                                "signal_id": sid,
                                "symbol": exp["symbol"],
                                "score": exp["score"],
                                "created_at": exp["created_at"],
                                "expected_fill": exp["fill"],
                                "expected_stop": exp["stop"],
                                "db_status": dbp.status if dbp else "absent",
                                "db_notes": (dbp.notes or "")[:100] if dbp else None,
                            }
                        )
                    continue

                # geometry check
                issues = []
                if not _close(float(dbp.entry_price), exp["fill"]):
                    issues.append(
                        f"entry db={float(dbp.entry_price)} exp={exp['fill']}"
                    )
                if not _close(float(dbp.stop_loss), exp["stop"]):
                    issues.append(
                        f"stop db={float(dbp.stop_loss)} exp={exp['stop']}"
                    )
                # edge ref should not equal mid if mid available
                if issues:
                    report["diffs"]["geometry_mismatch"].append(
                        {
                            "signal_id": sid,
                            "symbol": exp["symbol"],
                            "issues": issues,
                            "db_entry": float(dbp.entry_price),
                            "db_stop": float(dbp.stop_loss),
                            "exp_fill": exp["fill"],
                            "exp_stop": exp["stop"],
                            "edge_ref": exp["edge_ref"],
                            "status": dbp.status,
                        }
                    )

            # Extra DB fills not in expected
            for p in db_filled:
                if p.signal_id is None:
                    continue
                if int(p.signal_id) not in expected_by_sig:
                    report["diffs"]["extra_fills"].append(
                        {
                            "signal_id": p.signal_id,
                            "symbol": p.symbol,
                            "status": p.status,
                            "entry": float(p.entry_price),
                            "stop": float(p.stop_loss),
                            "pnl": float(p.realized_pnl or 0),
                            "notes": (p.notes or "")[:100],
                            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                        }
                    )

            # Live pendings outside allowlist (post-rebuild scan) — note only
            live_pending_outside = [
                {
                    "symbol": p.symbol,
                    "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                    "score": float(p.signal_score or 0),
                    "entry": float(p.entry_price),
                    "stop": float(p.stop_loss),
                    "notes": (p.notes or "")[:80],
                }
                for p in pendings
                if p.symbol.upper() not in allow
            ]
            report["live_pending_outside_allowlist"] = live_pending_outside

            # Allowlist pending geometry: edge stored?
            pending_geom = []
            for p in pendings:
                if p.symbol.upper() not in allow:
                    continue
                notes = p.notes or ""
                pending_geom.append(
                    {
                        "symbol": p.symbol,
                        "entry_ref": float(p.entry_price),
                        "stop": float(p.stop_loss),
                        "has_zone_prices": "zone=" in notes and "ATR" not in notes.split("zone=")[1].split(";")[0],
                        "notes": notes[:100],
                    }
                )
            report["allowlist_pending_geometry"] = pending_geom

            missing = report["diffs"]["missing_fills"]
            geom = report["diffs"]["geometry_mismatch"]
            extra = report["diffs"]["extra_fills"]
            should = report["signal_audit"]["should_have_traded"]

            report["verdict"] = {
                "account_identity_ok": identity_ok,
                "expected_fills": len(expected_fills),
                "db_closed_allowlist": len(
                    [p for p in closed if p.symbol.upper() in allow]
                ),
                "missing_fills": len(missing),
                "geometry_mismatches": len(geom),
                "extra_fills": len(extra),
                "should_have_traded": len(should),
                "FINAL_OK": identity_ok
                and len(missing) == 0
                and len(geom) == 0
                and len(should) == 0,
                # extras may be mid-rebuild leftovers; flag separately
                "WARN_EXTRA_FILLS": len(extra) > 0,
            }

        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        close = getattr(provider, "close", None)
        if close:
            await close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
