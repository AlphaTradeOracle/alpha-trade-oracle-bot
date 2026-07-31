#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only origin main

# Patch .env interval
if grep -q '^SCAN_INTERVAL_MINUTES=' .env; then
  sed -i 's/^SCAN_INTERVAL_MINUTES=.*/SCAN_INTERVAL_MINUTES=15/' .env
else
  echo 'SCAN_INTERVAL_MINUTES=15' >> .env
fi

echo "== env =="
grep '^SCAN_INTERVAL_MINUTES=' .env

# Disable legacy 30m/60m scan jobs so claim/register stays clean
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "UPDATE scheduled_jobs SET is_enabled=false, updated_at=now() WHERE job_key IN ('market_scan:30m','market_scan:60m') AND is_enabled=true;"

docker compose build app worker
docker compose up -d --force-recreate app worker
sleep 5

echo "== settings =="
docker compose exec -T worker python -c "from app.core.config import get_settings; s=get_settings(); print('scan_interval_minutes', s.scan_interval_minutes)"

echo "== jobs =="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT job_key, is_enabled, interval_seconds/60 AS mins, last_status, next_run_at FROM scheduled_jobs WHERE job_type='market_scan' ORDER BY job_key;"

echo "== worker =="
docker compose logs worker --tail 25
echo Done.
