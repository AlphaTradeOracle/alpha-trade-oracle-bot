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

upsert_env UNIVERSE_MAX_RANK 300
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
  'from app.core.config import get_settings; s=get_settings(); print(s.universe_max_rank, s.universe_scan_batch_size, s.universe_exchanges, s.universe_ticker_fallback_max, s.scan_interval_minutes)'

echo "=== Universe refresh ==="
docker compose exec -T worker python -m app.cli universe refresh

set -a
. ./.env
set +a
echo "=== Pool size rank<=300 ==="
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FILTER (WHERE in_universe AND is_active AND market_cap_rank <= 300) AS scan_pool_300,
          count(*) FILTER (WHERE exchange='kucoin' AND in_universe AND is_active AND market_cap_rank <= 300) AS kucoin_n,
          count(*) FILTER (WHERE exchange='binance' AND in_universe AND is_active AND market_cap_rank <= 300) AS binance_n,
          count(*) FILTER (WHERE exchange='coinbase' AND in_universe AND is_active AND market_cap_rank <= 300) AS coinbase_n
   FROM assets;"
