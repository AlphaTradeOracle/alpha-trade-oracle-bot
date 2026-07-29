#!/bin/bash
set -eu
cd /opt/alpha-trade-oracle-bot

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

# Top-300 handelbare USD*/USDT/USDC-Paare nach MCAP (Rank kann >300 sein).
upsert_env UNIVERSE_TARGET_COUNT 300
upsert_env UNIVERSE_MAX_RANK 0
upsert_env UNIVERSE_SCAN_BATCH_SIZE 300
upsert_env UNIVERSE_TICKER_FALLBACK_MAX 80
upsert_env UNIVERSE_EXCHANGES kucoin,binance,coinbase
upsert_env UNIVERSE_TICKER_FALLBACK true
upsert_env ENABLE_UNIVERSE_SCAN true
upsert_env SCAN_INTERVAL_MINUTES 60

# Sync code files needed for defaults (optional; env drives live)
if [ -f /tmp/config.py.lf ]; then
  docker cp /tmp/config.py.lf alpha-trade-oracle-worker:/opt/venv/lib/python3.12/site-packages/app/core/config.py
  docker cp /tmp/config.py.lf alpha-trade-oracle-worker:/app/app/core/config.py
  docker cp /tmp/config.py.lf alpha-trade-oracle-app:/opt/venv/lib/python3.12/site-packages/app/core/config.py 2>/dev/null || true
  docker cp /tmp/config.py.lf alpha-trade-oracle-app:/app/app/core/config.py 2>/dev/null || true
  cp /tmp/config.py.lf app/core/config.py
fi

docker compose up -d worker app
sleep 8

echo "=== Effective settings ==="
docker compose exec -T worker python -c \
  'from app.core.config import get_settings; s=get_settings(); print(s.universe_target_count, s.universe_max_rank, s.universe_scan_batch_size, s.universe_exchanges, s.universe_ticker_fallback_max, s.scan_interval_minutes)'

echo "=== Universe refresh ==="
docker compose exec -T worker python -m app.cli universe refresh

echo "=== Prune to top target_count by MCAP ==="
docker compose exec -T worker python -m app.cli data prune

set -a
. ./.env
set +a
echo "=== Active scan pool ==="
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FILTER (WHERE in_universe AND is_active) AS scan_pool,
          min(market_cap_rank) FILTER (WHERE in_universe AND is_active) AS min_rank,
          max(market_cap_rank) FILTER (WHERE in_universe AND is_active) AS max_rank,
          count(*) FILTER (WHERE exchange='kucoin' AND in_universe AND is_active) AS kucoin_n,
          count(*) FILTER (WHERE exchange='binance' AND in_universe AND is_active) AS binance_n,
          count(*) FILTER (WHERE exchange='coinbase' AND in_universe AND is_active) AS coinbase_n
   FROM assets;"
