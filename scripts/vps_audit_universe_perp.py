"""Audit in_universe symbols vs leverage coverage + PerpRouter.

Prints JSON: counts, missing symbols, sample.
Run inside app container:
  python /app/scripts/vps_audit_universe_perp.py
"""

from __future__ import annotations

import asyncio
import json
import sys


async def main() -> int:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.database.session import session_scope
    from app.market_data.leverage_coverage import LeverageCoverageClient, base_has_leverage
    from app.market_data.factory import create_paper_price_provider
    from app.models.market import Asset

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    lev = LeverageCoverageClient(settings)
    router = create_paper_price_provider(settings)
    try:
        bases = await lev.fetch_tradable_bases()
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Asset.symbol, Asset.base_asset, Asset.market_cap_rank)
                    .where(Asset.in_universe.is_(True))
                    .order_by(Asset.market_cap_rank.asc().nulls_last())
                )
            ).all()

        no_leverage: list[dict] = []
        no_route: list[dict] = []
        ok = 0
        for symbol, base, rank in rows:
            b = (base or symbol.replace("USDT", "").replace("USDC", "")).upper()
            has_lev = base_has_leverage(b, bases)
            routed = None
            route_err = None
            try:
                venue = await router.resolve_venue(symbol)
                routed = venue.name
                # also probe a short candle fetch
                await router.get_candles(symbol, "15m", limit=3)
            except Exception as exc:
                route_err = f"{type(exc).__name__}: {exc}"

            if not has_lev or route_err:
                item = {
                    "symbol": symbol,
                    "base": b,
                    "rank": rank,
                    "has_leverage": has_lev,
                    "venue": routed,
                    "error": route_err,
                }
                if not has_lev:
                    no_leverage.append(item)
                if route_err:
                    no_route.append(item)
            else:
                ok += 1

        out = {
            "in_universe": len(rows),
            "leverage_bases": len(bases),
            "ok_perp_route_and_candles": ok,
            "missing_leverage_count": len(no_leverage),
            "missing_route_or_candles_count": len(no_route),
            "missing_leverage": no_leverage,
            "missing_route_or_candles": no_route,
            "require_leverage": getattr(settings, "universe_require_leverage", None),
            "venues": getattr(settings, "universe_leverage_venues", None),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0
    finally:
        await lev.aclose()
        close = getattr(router, "close", None)
        if close:
            await close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
