#!/usr/bin/env bash
# Deploy perpetual price router for paper fills / TP / SL.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy perp prices ====="
git fetch origin main
git reset --hard origin/main
echo "bot=$(git rev-parse --short HEAD)"

# Idempotent env toggles
if grep -q '^PAPER_USE_PERP_PRICES=' .env; then
  sed -i 's/^PAPER_USE_PERP_PRICES=.*/PAPER_USE_PERP_PRICES=true/' .env
else
  echo 'PAPER_USE_PERP_PRICES=true' >> .env
fi
if grep -q '^PAPER_PERP_VENUES=' .env; then
  sed -i 's/^PAPER_PERP_VENUES=.*/PAPER_PERP_VENUES=binance,kucoin,aster,hyperliquid/' .env
else
  echo 'PAPER_PERP_VENUES=binance,kucoin,aster,hyperliquid' >> .env
fi
grep -E '^(PAPER_USE_PERP_PRICES|PAPER_PERP_VENUES)=' .env

docker compose build app worker
docker compose up -d --no-deps app worker
sleep 5
docker compose ps
docker compose exec -T app python - <<'PY'
import asyncio
from app.market_data.perp_router import PerpRouterProvider

async def main():
    r = PerpRouterProvider()
    samples = ["BTCUSDT", "DODOUSDT", "ANSEMUSDT", "MNTUSDT"]
    for sym in samples:
        v = await r.resolve_venue(sym)
        px = await r.get_price(sym)
        c = await r.get_candles(sym, "5m", limit=1, include_unclosed=True)
        print(f"ok {sym} venue={v.name} px={px} candles={len(c.candles)} src={c.source}")
    await r.close()

asyncio.run(main())
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy perp prices done ====="
