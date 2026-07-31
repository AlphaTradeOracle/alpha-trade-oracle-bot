#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
git pull --ff-only origin main
sed -i 's/^SIGNAL_MIN_ADX=.*/SIGNAL_MIN_ADX=30/' .env
grep '^SIGNAL_MIN_ADX=' .env
docker compose up -d --force-recreate worker app
sleep 5
docker compose exec -T worker python -c 'from app.core.config import get_settings; print("min_adx", get_settings().signal_min_adx)'
docker compose logs worker --tail 15
