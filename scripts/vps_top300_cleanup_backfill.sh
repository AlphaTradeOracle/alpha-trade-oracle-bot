#!/bin/bash
set -eu
cd /opt/alpha-trade-oracle-bot

git fetch origin main
git reset --hard origin/main

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

upsert_env UNIVERSE_TARGET_COUNT 300
upsert_env UNIVERSE_MAX_RANK 0
upsert_env UNIVERSE_SCAN_BATCH_SIZE 300
upsert_env UNIVERSE_EXCHANGES kucoin,binance,coinbase
upsert_env UNIVERSE_TICKER_FALLBACK true
upsert_env UNIVERSE_TICKER_FALLBACK_MAX 80
upsert_env CANDLE_RETENTION_DAYS 365
upsert_env SCAN_INTERVAL_MINUTES 60
upsert_env ENABLE_UNIVERSE_SCAN true

docker compose build worker app
docker compose up -d worker app
sleep 10

echo "=== settings ==="
docker compose exec -T worker python -c 'from app.core.config import get_settings; s=get_settings(); print(s.universe_target_count, s.universe_max_rank, s.universe_scan_batch_size, s.candle_retention_days, s.universe_exchanges)'

echo "=== universe refresh ==="
docker compose exec -T worker python -m app.cli universe refresh

echo "=== prune (top 300 mapped by MCAP) ==="
docker compose exec -T worker python -m app.cli data prune

set -a; . ./.env; set +a
echo "=== pool ==="
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FILTER (WHERE in_universe AND is_active) AS scan_pool,
          min(market_cap_rank) FILTER (WHERE in_universe AND is_active) AS min_rank,
          max(market_cap_rank) FILTER (WHERE in_universe AND is_active) AS max_rank,
          count(*) FILTER (WHERE exchange='kucoin' AND in_universe AND is_active) AS kucoin_n,
          count(*) FILTER (WHERE exchange='binance' AND in_universe AND is_active) AS binance_n,
          count(*) FILTER (WHERE exchange='coinbase' AND in_universe AND is_active) AS coinbase_n,
          (SELECT count(*) FROM market_candles) AS candles
   FROM assets;"

echo "=== start history backfill detached ==="
docker compose exec -d worker python -m app.cli data backfill --days 365
echo "log via: docker compose logs -f worker | grep history_backfill"
