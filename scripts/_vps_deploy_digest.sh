#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only origin main

# Ensure digest env flags
if grep -q '^PAPER_HOURLY_DIGEST_ENABLED=' .env; then
  sed -i 's/^PAPER_HOURLY_DIGEST_ENABLED=.*/PAPER_HOURLY_DIGEST_ENABLED=true/' .env
else
  echo 'PAPER_HOURLY_DIGEST_ENABLED=true' >> .env
fi
if grep -q '^PAPER_DIGEST_INTERVAL_MINUTES=' .env; then
  sed -i 's/^PAPER_DIGEST_INTERVAL_MINUTES=.*/PAPER_DIGEST_INTERVAL_MINUTES=60/' .env
else
  echo 'PAPER_DIGEST_INTERVAL_MINUTES=60' >> .env
fi

grep -E '^PAPER_(HOURLY_DIGEST|DIGEST_INTERVAL)' .env

docker compose build app worker
docker compose up -d --force-recreate app worker
sleep 6

echo "== settings =="
docker compose exec -T worker python -c "from app.core.config import get_settings; s=get_settings(); print('digest', s.paper_hourly_digest_enabled, s.paper_digest_interval_minutes)"

echo "== jobs =="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT job_key, is_enabled, interval_seconds/60 AS mins, last_status FROM scheduled_jobs ORDER BY job_key;"

echo "== send digest now =="
docker compose exec -T worker python -m app.cli paper digest --send

echo "== worker =="
docker compose logs worker --tail 30
echo Done.
