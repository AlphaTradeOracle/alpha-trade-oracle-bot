"""Reset paper ledger and rebuild from signals for current in_universe assets.

Used after Top-400 leverage activation to retrospectively include signals that
would have been paper-traded under the new universe.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.container import build_container
from app.core.logging import configure_logging, get_logger
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.market import Asset
from app.repositories.asset_repository import AssetRepository
from app.repositories.paper_repository import PaperRepository
from app.repositories.signal_repository import SignalRepository
from app.services.paper_trading_service import (
    PORTFOLIO_LIMIT_SKIPS,
    PaperBackfillResult,
    PaperRebuildResult,
)
from app.signals.retest_entry import RetestArmResult, arm_retest_entry

logger = get_logger(__name__)


async def _universe_symbols(session) -> set[str]:
    rows = await session.execute(select(Asset.symbol).where(Asset.in_universe.is_(True)))
    return {str(s).upper() for s in rows.scalars().all()}


async def rebuild_universe_only(
    *,
    since: datetime,
    dispatched_only: bool,
) -> PaperRebuildResult:
    from datetime import timedelta

    from app.scheduler.jobs import _collect_prices

    container = build_container()
    settings = container.settings
    configure_logging(settings.log_level, json_output=False)
    paper = container.paper_trading
    provider = container.provider
    providers = container.universe_providers
    out = PaperRebuildResult()

    async with session_scope() as session:
        account = await paper.get_or_create_account(session)
        repo = PaperRepository(session)
        out.reset_positions = await repo.reset_ledger(account)
        allowed = await _universe_symbols(session)
        logger.info("paper_rebuild_universe_only_start", since=since.isoformat(), universe=len(allowed))

        backfill = PaperBackfillResult()
        with paper._without_notifications():
            signals = await SignalRepository(session).list_since(
                since,
                actionable_only=True,
                dispatched_only=dispatched_only,
                limit=10_000,
            )
            asset_ids = list({s.asset_id for s in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
            cfg = paper._retest_config()
            lookback_pad = timedelta(days=14)
            cutoff = utc_now()
            ordered = sorted(signals, key=lambda s: s.created_at)

            for signal in ordered:
                backfill.considered += 1
                symbol = symbols_by_id.get(signal.asset_id)
                if not symbol:
                    backfill.skipped_filters += 1
                    continue
                symbol = symbol.upper()
                if symbol not in allowed:
                    backfill.skipped_filters += 1
                    continue
                if not paper._passes_paper_gates(signal):
                    backfill.skipped_filters += 1
                    continue

                # Historische Sperre: auch bereits geschlossene Trades blocken,
                # solange signal.created_at in deren Lifetime lag (Rebuild spielt
                # Exits voraus und wuerde sonst Overlaps erzeugen).
                if await repo.is_symbol_busy_at(
                    account.id, symbol, ensure_utc(signal.created_at)
                ):
                    backfill.skipped_existing += 1
                    continue

                position = await paper.open_from_stored_signal(
                    session,
                    signal,
                    symbol=symbol,
                    extend_expiry=not paper.retest_enabled,
                )
                if position is None:
                    if paper._last_skip_reason in PORTFOLIO_LIMIT_SKIPS:
                        backfill.skipped_limits += 1
                    else:
                        backfill.skipped_cash += 1
                    continue

                backfill.opened += 1
                assert backfill.opened_symbols is not None
                if symbol not in backfill.opened_symbols:
                    backfill.opened_symbols.append(symbol)

                tf = position.timeframe or "1h"
                if position.status != "pending":
                    try:
                        series_mgmt = await provider.get_candles(
                            symbol,
                            tf,
                            limit=100_000,
                            start_time=position.opened_at,
                            end_time=cutoff,
                        )
                        await paper._replay_bars(
                            session, account, position, series_mgmt.candles
                        )
                        out.replayed += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "paper_rebuild_replay_failed", symbol=symbol, error=str(exc)
                        )
                    continue

                backfill.pending += 1
                try:
                    series = await provider.get_candles(
                        symbol,
                        tf,
                        limit=100_000,
                        start_time=ensure_utc(position.opened_at) - lookback_pad,
                        end_time=cutoff,
                    )
                    candles = (
                        list(series.candles)
                        if series is not None and not series.is_empty
                        else []
                    )
                except Exception as exc:  # noqa: BLE001
                    await paper._cancel_pending_retest(
                        session,
                        position,
                        RetestArmResult(status="skipped_no_history", note=str(exc)),
                    )
                    out.retest_skipped += 1
                    continue

                arm = arm_retest_entry(
                    direction=position.direction,
                    arm_time=position.opened_at,
                    reference_entry=float(position.entry_price),
                    original_stop=float(position.stop_loss),
                    timeframe=tf,
                    candles=candles,
                    config=cfg,
                )
                if (
                    arm.filled
                    and arm.fill_price is not None
                    and arm.fill_time is not None
                    and arm.stop is not None
                ):
                    ok = await paper._activate_pending_retest(
                        session, account, position, arm
                    )
                    if not ok:
                        out.retest_skipped += 1
                        continue
                    out.retest_filled += 1
                    try:
                        series_mgmt = await provider.get_candles(
                            symbol,
                            tf,
                            limit=100_000,
                            start_time=position.opened_at,
                            end_time=cutoff,
                        )
                        await paper._replay_bars(
                            session, account, position, series_mgmt.candles
                        )
                        out.replayed += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "paper_rebuild_replay_failed",
                            symbol=symbol,
                            error=str(exc),
                        )
                else:
                    await paper._cancel_pending_retest(session, position, arm)
                    out.retest_skipped += 1

            still_open = await repo.list_open_positions(account.id)
            out.still_open = len(still_open)
            if still_open:
                symbols = [p.symbol for p in still_open]
                prices = await _collect_prices(provider, symbols, providers=providers)
                await paper.update_open_positions(session, prices)
                still_open = await repo.list_open_positions(account.id)
                out.still_open = len(still_open)

        out.backfill = backfill
        summary = await paper.summary(session)

    await container.aclose()
    print(
        f"reset={out.reset_positions} considered={backfill.considered} "
        f"opened={backfill.opened} limits={backfill.skipped_limits} "
        f"filters={backfill.skipped_filters} existing={backfill.skipped_existing} "
        f"retest_filled={out.retest_filled} retest_skipped={out.retest_skipped} "
        f"replayed={out.replayed} still_open={out.still_open}"
    )
    print(
        f"equity=${summary.equity:,.2f} realized=${summary.realized_pnl:,.2f} "
        f"closed={summary.closed_trades} pending={summary.pending_positions} "
        f"open={summary.open_positions}"
    )
    if backfill.opened_symbols:
        print("symbols:", ", ".join(backfill.opened_symbols[:60]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since",
        default="2026-07-31T16:32:35+00:00",
        help="ISO UTC timestamp (paper reset / earliest window)",
    )
    parser.add_argument("--dispatched-only", action="store_true")
    args = parser.parse_args()
    since = ensure_utc(datetime.fromisoformat(args.since))
    asyncio.run(rebuild_universe_only(since=since, dispatched_only=args.dispatched_only))


if __name__ == "__main__":
    main()
