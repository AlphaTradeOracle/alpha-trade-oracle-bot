#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only origin main

if grep -q '^TP_MULTIPLIERS=' .env; then
  sed -i 's/^TP_MULTIPLIERS=.*/TP_MULTIPLIERS=1.5,2.5,4.0/' .env
else
  echo 'TP_MULTIPLIERS=1.5,2.5,4.0' >> .env
fi

grep '^TP_MULTIPLIERS=\|^SIGNAL_MIN_ADX=' .env

docker compose up -d --build worker
docker compose exec -T worker python -c '
from app.core.config import get_settings
s = get_settings()
print("tp", s.parsed_tp_multipliers)
print("adx", s.signal_min_adx)
assert s.parsed_tp_multipliers == (1.5, 2.5, 4.0), s.parsed_tp_multipliers
assert s.signal_min_adx == 30.0, s.signal_min_adx
print("OK live config")
'
