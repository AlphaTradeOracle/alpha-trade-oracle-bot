"""Count how many universe coins have perp markets per venue."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import session_scope
from app.market_data.leverage_coverage import (
    LeverageCoverageClient,
    base_has_leverage,
    normalize_base,
)
from app.models.market import Asset


async def main() -> None:
    settings = get_settings()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol, Asset.base_asset).where(Asset.in_universe.is_(True))
            )
        ).all()

    client = LeverageCoverageClient(settings)
    try:
        venues: dict[str, set[str]] = {}
        for name in ("binance", "kucoin", "aster", "hyperliquid"):
            venues[name] = await client._load_venue(name)
            print(f"venue_{name}={len(venues[name])}")
    finally:
        await client.aclose()

    n = len(rows)
    counts = {k: 0 for k in venues}
    only = {k: 0 for k in venues}
    multi = 0
    not_binance: list[tuple[str, str, list[str]]] = []

    for sym, base in rows:
        b = normalize_base(base or sym.replace("USDT", ""))
        hits = [v for v, s in venues.items() if base_has_leverage(b, s)]
        for v in hits:
            counts[v] += 1
        if len(hits) == 1:
            only[hits[0]] += 1
        elif len(hits) > 1:
            multi += 1
        if not base_has_leverage(b, venues["binance"]):
            not_binance.append((sym, b, hits))

    print(f"universe={n}")
    print(f"on_venue={counts}")
    print(f"only_on_venue={only}")
    print(f"on_multiple_venues={multi}")
    for name, c in counts.items():
        print(f"{name}_perp={c} pct={100 * c / max(n, 1):.1f}")
    print(f"not_on_binance_perp={len(not_binance)}")
    print("sample_not_binance=", not_binance[:25])


if __name__ == "__main__":
    asyncio.run(main())
