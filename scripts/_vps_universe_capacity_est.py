"""Estimate mapped count if CoinGecko pagination returned full UNIVERSE_SIZE.

Uses already-fetched leverage bases + exchange indices; fetches CG pages
manually (ignoring the early-stop bug) then applies the same filters.
"""

from __future__ import annotations

import asyncio
import json


async def main() -> int:
    from app.core.config import get_settings
    from app.core.errors import SymbolNotFoundError
    from app.core.logging import configure_logging
    from app.database.redis_client import get_redis
    from app.market_data.coingecko import CoinGeckoClient, MAX_PER_PAGE
    from app.market_data.factory import create_paper_price_provider, create_universe_providers
    from app.market_data.leverage_coverage import LeverageCoverageClient, base_has_leverage
    from app.services.universe_service import SKIP_BASE_ASSETS, UniverseService

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    redis = get_redis(settings)
    providers = create_universe_providers(settings, redis_client=redis)
    cg = CoinGeckoClient(settings)
    lev = LeverageCoverageClient(settings)
    perp = create_paper_price_provider(settings)
    svc = UniverseService(providers, cg, settings=settings, leverage=lev, perp_provider=perp)

    quote = settings.default_quote_asset.upper()
    limit = settings.universe_size
    target = settings.universe_target_count

    # Bypass buggy early-stop: always pull ceil(limit/MAX_PER_PAGE) pages.
    import math

    pages_needed = max(1, math.ceil(limit / MAX_PER_PAGE))
    markets = []
    for page in range(1, pages_needed + 1):
        batch = await cg._fetch_markets_page(page=page, per_page=MAX_PER_PAGE)
        if not batch:
            break
        markets.extend(batch)
    markets = markets[:limit]
    page = pages_needed

    bases = await lev.fetch_tradable_bases()
    indices = await svc._load_exchange_indices(quote)

    mapped = 0
    reasons = {"stable": 0, "no_lev": 0, "no_pair": 0, "no_route": 0, "no_candles": 0, "ok": 0}
    seen: set[str] = set()
    for market in markets:
        if mapped >= target:
            break
        base = market.symbol.upper().strip()
        if not base or base in SKIP_BASE_ASSETS or base == quote:
            reasons["stable"] += 1
            continue
        if not base_has_leverage(base, bases):
            reasons["no_lev"] += 1
            continue
        mapped_info = svc._map_direct(market, quote, indices)
        if mapped_info is None:
            reasons["no_pair"] += 1
            continue
        symbol, info, exchange = mapped_info
        if symbol in seen:
            continue
        mb = (info.base_asset or "").upper().strip()
        if not base_has_leverage(mb, bases):
            reasons["no_lev"] += 1
            continue
        try:
            await perp.resolve_venue(symbol)
        except SymbolNotFoundError:
            reasons["no_route"] += 1
            continue
        except Exception:
            pass
        verdict = await svc._verify_tradability(symbol, exchange)
        if verdict != "ok":
            reasons["no_candles"] += 1
            continue
        seen.add(symbol)
        mapped += 1
        reasons["ok"] += 1

    print(json.dumps({
        "cg_markets_manual_pages": len(markets),
        "pages_fetched": page,
        "leverage_bases": len(bases),
        "would_map_to_target": mapped,
        "target": target,
        "target_reached": mapped >= target,
        "reasons": reasons,
        "max_rank_in_pool": max((m.market_cap_rank for m in markets), default=None),
    }, indent=2))

    await lev.aclose()
    await cg.close()
    await perp.close()
    for p in providers.values():
        c = getattr(p, "close", None)
        if c:
            await c()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
