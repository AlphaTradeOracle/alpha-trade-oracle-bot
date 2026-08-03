"""Probe CoinGecko top-markets pagination on VPS."""

from __future__ import annotations

import asyncio
import json


async def main() -> int:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.market_data.coingecko import CoinGeckoClient, MAX_PER_PAGE

    configure_logging("INFO", json_output=False)
    settings = get_settings()
    cg = CoinGeckoClient(settings)
    try:
        key = settings.coingecko_api_key.get_secret_value().strip()
        info = {
            "base_url": settings.coingecko_base_url,
            "has_api_key": bool(key),
            "key_len": len(key),
            "max_per_page": MAX_PER_PAGE,
            "universe_size": settings.universe_size,
        }
        # Page probes
        pages = {}
        for page in (1, 2, 3, 4, 5, 6):
            try:
                batch = await cg._fetch_markets_page(page=page, per_page=MAX_PER_PAGE)
                pages[str(page)] = {
                    "count": len(batch),
                    "first": batch[0].symbol if batch else None,
                    "last": batch[-1].symbol if batch else None,
                    "first_rank": batch[0].market_cap_rank if batch else None,
                    "last_rank": batch[-1].market_cap_rank if batch else None,
                }
            except Exception as exc:
                pages[str(page)] = {"error": f"{type(exc).__name__}: {exc}"}
                break

        markets = await cg.fetch_top_markets(settings.universe_size)
        info["pages"] = pages
        info["fetch_top_markets_count"] = len(markets)
        info["fetch_min_rank"] = markets[0].market_cap_rank if markets else None
        info["fetch_max_rank"] = markets[-1].market_cap_rank if markets else None
        print(json.dumps(info, indent=2, default=str))
        return 0
    finally:
        await cg.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
