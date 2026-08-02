#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git fetch origin main
git reset --hard origin/main
if grep -q '^PAPER_HOURLY_DIGEST_ENABLED=' .env; then
  sed -i 's/^PAPER_HOURLY_DIGEST_ENABLED=.*/PAPER_HOURLY_DIGEST_ENABLED=false/' .env
else
  echo 'PAPER_HOURLY_DIGEST_ENABLED=false' >> .env
fi
docker compose build worker
docker compose up -d --no-deps worker
docker compose exec -T worker python -c \
  'from app.core.config import get_settings; s=get_settings(); print("digest_enabled", s.paper_hourly_digest_enabled)'
docker compose logs worker --tail 30 | grep -E 'paper_digest|scheduler_jobs|Job' || true
echo "digest disabled"
