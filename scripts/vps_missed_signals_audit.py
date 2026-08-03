"""Find qualifying signals since reset that bugs would have blocked or distorted.

Compares mid-reference retest (old bug) vs edge-reference (fixed) on perp candles.
Also lists signals with no successful paper fill/open in the rebuilt book.

Run: python /app/scripts/vps_missed_signals_audit.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from decimal import Decimal


SINCE = "2026-07-31T16:32:35+00:00"


async def main() -> int:
    from sqlalchemy import select

    from app.container import build_container
    from app.core.config import get_settings
    from app.core.enums import SignalDirection
    from app.core.logging import configure_logging
    from app.core.time import ensure_utc
    from app.database.session import session_scope
    from app.models.market import Asset
    from app.models.paper import PaperPosition
    from app.models.signal import Signal
    from app.repositories.asset_repository import AssetRepository
    from app.repositories.signal_repository import SignalRepository
    from app.signals.retest_entry import (
        RetestEntryConfig,
        arm_retest_entry,
        retest_zone,
        zone_overlaps_stop,
        wilder_atr,
        idx_at_or_before,
    )

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    container = build_container()
    provider = container.paper_price_provider
    since = ensure_utc(__import__("datetime").datetime.fromisoformat(SINCE))
    cfg = RetestEntryConfig(
        zone_near=Decimal(str(settings.paper_retest_zone_near)),
        zone_far=Decimal(str(settings.paper_retest_zone_far)),
        pending_multiplier=int(settings.paper_retest_pending_multiplier),
        min_bars_in_zone=int(settings.paper_retest_min_bars_in_zone),
    )
    lookback = timedelta(days=14)
    cutoff = ensure_utc(__import__("datetime").datetime.now(__import__("datetime").UTC))

    out: dict = {
        "since": SINCE,
        "gates": {
            "long_min": settings.signal_min_score,
            "short_max": settings.signal_short_max_score,
            "short_min": settings.signal_short_min_score,
        },
        "qualifying": 0,
        "mid_vs_edge": [],
        "no_perp_candles": [],
        "zone_stop_overlap_mid_only": [],
        "would_fill_edge_not_mid": [],
        "would_fill_mid_not_edge": [],
        "no_paper_fill_after_rebuild": [],
        "summary": {},
    }

    try:
        async with session_scope() as session:
            paper = container.paper_trading
            signals = await SignalRepository(session).list_since(
                since, actionable_only=True, dispatched_only=False, limit=5000
            )
            asset_ids = list({s.asset_id for s in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)

            # Paper outcomes after rebuild (by signal_id)
            pos_rows = (
                await session.execute(
                    select(
                        PaperPosition.signal_id,
                        PaperPosition.symbol,
                        PaperPosition.status,
                        PaperPosition.realized_pnl,
                        PaperPosition.notes,
                    ).where(PaperPosition.signal_id.is_not(None))
                )
            ).all()
            by_signal: dict[int, list] = {}
            for sid, sym, status, pnl, notes in pos_rows:
                by_signal.setdefault(int(sid), []).append(
                    {
                        "symbol": sym,
                        "status": status,
                        "pnl": float(pnl or 0),
                        "notes": (notes or "")[:120],
                    }
                )

            uni = set(
                (
                    await session.execute(
                        select(Asset.symbol).where(Asset.in_universe.is_(True))
                    )
                ).scalars()
            )

            for signal in sorted(signals, key=lambda s: s.created_at):
                if not paper._passes_paper_gates(signal):
                    continue
                symbol = symbols_by_id.get(signal.asset_id)
                if not symbol:
                    continue
                symbol = symbol.upper()
                out["qualifying"] += 1

                try:
                    direction = SignalDirection(signal.direction)
                except ValueError:
                    continue
                is_long = direction.is_long
                mid = None
                if signal.entry_low is not None and signal.entry_high is not None:
                    mid = (float(signal.entry_low) + float(signal.entry_high)) / 2.0
                edge = (
                    float(signal.entry_low)
                    if is_long and signal.entry_low is not None
                    else float(signal.entry_high)
                    if (not is_long) and signal.entry_high is not None
                    else mid
                )
                stop = float(signal.stop_loss) if signal.stop_loss is not None else None
                if edge is None or stop is None or mid is None:
                    continue

                tf = signal.primary_timeframe or "1h"
                try:
                    series = await provider.get_candles(
                        symbol,
                        tf,
                        limit=100_000,
                        start_time=ensure_utc(signal.created_at) - lookback,
                        end_time=cutoff,
                    )
                    candles = list(series.candles) if series and not series.is_empty else []
                except Exception as exc:
                    out["no_perp_candles"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "direction": signal.direction,
                            "created_at": signal.created_at.isoformat(),
                            "error": f"{type(exc).__name__}: {exc}",
                            "in_universe": symbol in uni,
                        }
                    )
                    continue

                if not candles:
                    out["no_perp_candles"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "direction": signal.direction,
                            "created_at": signal.created_at.isoformat(),
                            "error": "empty_candles",
                            "in_universe": symbol in uni,
                        }
                    )
                    continue

                sig_idx = idx_at_or_before(candles, ensure_utc(signal.created_at))
                atr = wilder_atr(candles, sig_idx, cfg.atr_period) if sig_idx is not None else None
                mid_overlap = False
                edge_overlap = False
                if atr and atr > 0:
                    mlo, mhi = retest_zone(
                        Decimal(str(mid)), Decimal(str(atr)), is_long=is_long,
                        zone_near=cfg.zone_near, zone_far=cfg.zone_far,
                    )
                    elo, ehi = retest_zone(
                        Decimal(str(edge)), Decimal(str(atr)), is_long=is_long,
                        zone_near=cfg.zone_near, zone_far=cfg.zone_far,
                    )
                    mid_overlap = zone_overlaps_stop(mlo, mhi, Decimal(str(stop)))
                    edge_overlap = zone_overlaps_stop(elo, ehi, Decimal(str(stop)))
                    if mid_overlap and not edge_overlap:
                        out["zone_stop_overlap_mid_only"].append(
                            {
                                "signal_id": signal.id,
                                "symbol": symbol,
                                "score": float(signal.score),
                                "direction": signal.direction,
                                "created_at": signal.created_at.isoformat(),
                                "mid": mid,
                                "edge": edge,
                                "stop": stop,
                                "atr": atr,
                            }
                        )

                arm_mid = arm_retest_entry(
                    direction=direction,
                    arm_time=signal.created_at,
                    reference_entry=mid,
                    original_stop=stop,
                    timeframe=tf,
                    candles=candles,
                    config=cfg,
                )
                arm_edge = arm_retest_entry(
                    direction=direction,
                    arm_time=signal.created_at,
                    reference_entry=edge,
                    original_stop=stop,
                    timeframe=tf,
                    candles=candles,
                    config=cfg,
                )

                if arm_edge.filled and not arm_mid.filled:
                    out["would_fill_edge_not_mid"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "direction": signal.direction,
                            "created_at": signal.created_at.isoformat(),
                            "mid_status": arm_mid.status,
                            "edge_fill": arm_edge.fill_price,
                            "edge_stop": arm_edge.stop,
                            "mid_note": arm_mid.note,
                            "in_universe": symbol in uni,
                        }
                    )
                if arm_mid.filled and not arm_edge.filled:
                    out["would_fill_mid_not_edge"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "direction": signal.direction,
                            "created_at": signal.created_at.isoformat(),
                            "edge_status": arm_edge.status,
                            "mid_fill": arm_mid.fill_price,
                            "in_universe": symbol in uni,
                        }
                    )

                # After rebuild: qualifying signal with edge fill potential but no open/closed paper
                papers = by_signal.get(int(signal.id), [])
                success = any(p["status"] in {"open", "closed", "pending"} for p in papers)
                if arm_edge.filled and not success:
                    out["no_paper_fill_after_rebuild"].append(
                        {
                            "signal_id": signal.id,
                            "symbol": symbol,
                            "score": float(signal.score),
                            "direction": signal.direction,
                            "created_at": signal.created_at.isoformat(),
                            "edge_fill": arm_edge.fill_price,
                            "paper": papers,
                            "in_universe": symbol in uni,
                            "likely_reason": (
                                "busy_or_limits_or_overlap_reject_or_filters"
                            ),
                        }
                    )

            out["summary"] = {
                "qualifying_signals": out["qualifying"],
                "no_perp_candles": len(out["no_perp_candles"]),
                "zone_stop_overlap_mid_only": len(out["zone_stop_overlap_mid_only"]),
                "would_fill_edge_not_mid": len(out["would_fill_edge_not_mid"]),
                "would_fill_mid_not_edge": len(out["would_fill_mid_not_edge"]),
                "edge_fill_but_no_paper_row": len(out["no_paper_fill_after_rebuild"]),
            }
        print(json.dumps(out, indent=2, default=str))
        return 0
    finally:
        close = getattr(provider, "close", None)
        if close:
            await close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
