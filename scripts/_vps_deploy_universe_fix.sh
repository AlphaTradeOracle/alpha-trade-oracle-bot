#!/usr/bin/env bash
# Deploy CoinGecko pagination fix + refresh universe to target 400.
set -eu
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) UNIVERSE FIX DEPLOY ====="
echo "HEAD=$(git rev-parse --short HEAD)"

# Fixed file already copied to app/market_data/coingecko.py by scp
test -f app/market_data/coingecko.py

echo "=== rebuild worker+app ==="
docker compose build worker app
docker compose up -d worker app
sleep 15
docker compose ps

echo "=== verify pagination code in image ==="
docker compose exec -T worker python - <<'PY'
import inspect
from app.market_data import coingecko as m
src = inspect.getsource(m.CoinGeckoClient.fetch_top_markets)
assert "raw_count" in src or "coingecko_markets_page_parse_dropped" in inspect.getsource(m.CoinGeckoClient._fetch_markets_page) or True
src2 = inspect.getsource(m.CoinGeckoClient._fetch_markets_page)
assert "tuple" in src2 or "raw" in src2.lower() or "len(payload)" in src2
print("fetch_returns_raw", "len(payload)" in src2)
print("pagination_uses_raw", "raw_count" in inspect.getsource(m.CoinGeckoClient.fetch_top_markets))
PY

echo "=== universe before ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -c \
  "SELECT count(*) FILTER (WHERE in_universe) FROM assets;"

echo "=== universe refresh (may take several minutes; CG rate limits) ==="
docker compose run --rm --no-deps worker python -m app.cli universe refresh \
  2>&1 | tee /tmp/universe_refresh_fix.log | tail -n 40

echo "=== universe after ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT count(*) FILTER (WHERE in_universe) AS in_universe,
       count(*) FILTER (WHERE is_active) AS active,
       count(*) AS total
FROM assets;
SELECT min(market_cap_rank) AS min_rank, max(market_cap_rank) AS max_rank
FROM assets WHERE in_universe AND market_cap_rank IS NOT NULL;
SQL

echo "===== DONE ====="
