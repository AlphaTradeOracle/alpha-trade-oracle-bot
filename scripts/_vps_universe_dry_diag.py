"""Dry-run universe skip-reason diagnosis (no DB writes).

Run inside app/worker container:
  python /app/scripts/_vps_universe_dry_diag.py
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter


async def main() -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.core.errors import SymbolNotFoundError
    from app.market_data.coingecko import CoinGeckoClient
    from app.market_data.factory import create_universe_providers, create_paper_price_provider
    from app.market_data.leverage_coverage import LeverageCoverageClient, base_has_leverage
    from app.services.universe_service import UniverseService, SKIP_BASE_ASSETS
    from app.database.redis_client import get_redis

    configure_logging("WARNING", json_output=False)
    settings = get_settings()
    redis = get_redis(settings)
    providers = create_universe_providers(settings, redis_client=redis)
    cg = CoinGeckoClient(settings)
    lev = LeverageCoverageClient(settings)
    perp = create_paper_price_provider(settings) if settings.universe_require_leverage else None
    svc = UniverseService(
        providers, cg, settings=settings, leverage=lev, perp_provider=perp
    )

    quote = settings.default_quote_asset.upper()
    limit = settings.universe_size
    target = max(0, settings.universe_target_count)
    ticker_budget = max(0, settings.universe_ticker_fallback_max)

    markets = await cg.fetch_top_markets(limit)
    leverage_bases = None
    if settings.universe_require_leverage:
        leverage_bases = await lev.fetch_tradable_bases()

    exchange_indices = await svc._load_exchange_indices(quote)
    reasons: Counter[str] = Counter()
    mapped = 0
    mapped_symbols: set[str] = set()
    samples: dict[str, list[str]] = {k: [] for k in (
        "stable", "no_leverage_cg", "no_pair", "duplicate",
        "no_leverage_mapped", "no_perp_route", "no_candles", "illiquid", "ok"
    )}

    def sample(key: str, label: str) -> None:
        if len(samples[key]) < 8:
            samples[key].append(label)

    for market in markets:
        if target > 0 and mapped >= target:
            reasons["stopped_at_target"] += 1
            break

        base = market.symbol.upper().strip()
        if not base or base in SKIP_BASE_ASSETS or base == quote:
            reasons["stable"] += 1
            sample("stable", f"{base}:{market.market_cap_rank}")
            continue

        if leverage_bases is not None and not base_has_leverage(base, leverage_bases):
            reasons["no_leverage_cg"] += 1
            sample("no_leverage_cg", f"{base}:{market.market_cap_rank}")
            continue

        mapped_info = svc._map_direct(market, quote, exchange_indices)
        via_ticker = False
        if mapped_info is None and settings.universe_ticker_fallback and ticker_budget > 0:
            mapped_info = await svc._map_via_tickers(market, quote, exchange_indices)
            if mapped_info is not None:
                via_ticker = True
                ticker_budget -= 1

        if mapped_info is None:
            reasons["no_pair"] += 1
            sample("no_pair", f"{base}:{market.market_cap_rank}")
            continue

        symbol, info, exchange = mapped_info
        if symbol in mapped_symbols:
            reasons["duplicate"] += 1
            sample("duplicate", symbol)
            continue

        if leverage_bases is not None:
            mapped_base = (info.base_asset or "").upper().strip()
            if not base_has_leverage(mapped_base, leverage_bases):
                reasons["no_leverage_mapped"] += 1
                sample("no_leverage_mapped", f"{symbol}:{mapped_base}")
                continue
            if perp is not None:
                resolve = getattr(perp, "resolve_venue", None)
                if resolve is not None:
                    try:
                        await resolve(symbol)
                    except SymbolNotFoundError:
                        reasons["no_perp_route"] += 1
                        sample("no_perp_route", symbol)
                        continue
                    except Exception:
                        pass

        verdict = await svc._verify_tradability(symbol, exchange)
        if verdict == "no_candles":
            reasons["no_candles"] += 1
            sample("no_candles", f"{symbol}@{exchange}")
            continue
        if verdict == "illiquid":
            reasons["illiquid"] += 1
            sample("illiquid", f"{symbol}@{exchange}")
            continue

        mapped += 1
        mapped_symbols.add(symbol)
        reasons["ok"] += 1
        sample("ok", f"{symbol}:r{market.market_cap_rank}")
        if via_ticker:
            reasons["ok_via_ticker"] += 1

    # How many CG markets remain after early stop?
    remaining = max(0, len(markets) - sum(
        reasons[k] for k in (
            "stable", "no_leverage_cg", "no_pair", "duplicate",
            "no_leverage_mapped", "no_perp_route", "no_candles", "illiquid", "ok"
        )
    ) - reasons.get("stopped_at_target", 0))

    out = {
        "config": {
            "universe_size": settings.universe_size,
            "universe_target_count": settings.universe_target_count,
            "universe_require_leverage": settings.universe_require_leverage,
            "universe_leverage_venues": settings.universe_leverage_venues,
            "universe_max_rank": settings.universe_max_rank,
            "universe_ticker_fallback": settings.universe_ticker_fallback,
            "universe_ticker_fallback_max": settings.universe_ticker_fallback_max,
            "universe_verify_candles": settings.universe_verify_candles,
            "universe_min_quote_volume_usd": settings.universe_min_quote_volume_usd,
            "universe_exchanges": settings.universe_exchanges,
            "default_quote": settings.default_quote_asset,
            "primary_timeframe": settings.primary_timeframe,
        },
        "ranked_from_coingecko": len(markets),
        "leverage_bases": len(leverage_bases) if leverage_bases is not None else None,
        "exchange_index_sizes": {ex: len(idx) for ex, idx in exchange_indices.items()},
        "would_map": mapped,
        "target": target,
        "target_reached": mapped >= target if target else True,
        "skip_reasons": dict(reasons),
        "unprocessed_after_loop": remaining,
        "samples": samples,
    }
    print(json.dumps(out, indent=2, default=str))

    await lev.aclose()
    await cg.close()
    if perp is not None:
        close = getattr(perp, "close", None)
        if close:
            await close()
    for p in providers.values():
        c = getattr(p, "close", None)
        if c:
            await c()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
