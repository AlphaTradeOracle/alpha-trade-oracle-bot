#!/usr/bin/env bash
# Deploy Top-400 leverage-filtered universe and refresh.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=exports/top400_leverage.log
mkdir -p exports
exec >>"$LOG" 2>&1

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) top400 leverage start ====="

git fetch origin main
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

set_env UNIVERSE_SIZE 1500
set_env UNIVERSE_TARGET_COUNT 400
set_env UNIVERSE_SCAN_BATCH_SIZE 400
set_env UNIVERSE_REQUIRE_LEVERAGE true
set_env UNIVERSE_LEVERAGE_VENUES binance,kucoin,aster,hyperliquid
set_env SCAN_INTERVAL_MINUTES 15

grep -E '^(UNIVERSE_|SCAN_INTERVAL)' .env | sort

ids=$(docker ps -aq --filter name=alpha-trade-oracle-worker || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi

docker compose build worker
docker compose up -d worker
sleep 8
docker compose ps worker

echo "----- universe refresh -----"
docker compose exec -T worker python -m app.cli universe refresh

echo "----- counts -----"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
          COUNT(*) FILTER (WHERE in_universe AND is_active) AS active_universe,
          MAX(market_cap_rank) FILTER (WHERE in_universe) AS max_rank
   FROM assets;"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) top400 leverage done ====="
