#!/usr/bin/env bash
set -euo pipefail
echo "=== DESK API ==="
curl -fsS "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" | python3 - <<'PY'
import json,sys
d=json.load(sys.stdin)
for c in d.get("coins") or []:
    print(f"{c.get('rank'):>3} {c.get('symbol'):<8} {c.get('name'):<22} mcap={c.get('marketCapUsd')} price={c.get('priceUsd')}")
print("generated", d.get("generatedAt"))
PY

echo "=== COINGECKO DIRECT (container) ==="
docker compose -f /opt/alpha-trade-oracle-bot/docker-compose.yml exec -T app python - <<'PY'
import asyncio
from app.market_data.coingecko import CoinGeckoClient
from app.core.config import get_settings

async def main():
    c = CoinGeckoClient(get_settings())
    try:
        markets = await c.fetch_live_markets(25)
        for m in markets:
            print(f"{m.market_cap_rank:>3} {m.symbol:<8} {m.name:<22} mcap={m.market_cap_usd} price={m.price_usd}")
    finally:
        await c.close()

asyncio.run(main())
PY
