#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

git pull --ff-only origin main

if grep -q '^SIGNAL_REQUIRE_STRONG=' .env; then
  sed -i 's/^SIGNAL_REQUIRE_STRONG=.*/SIGNAL_REQUIRE_STRONG=false/' .env
else
  echo 'SIGNAL_REQUIRE_STRONG=false' >> .env
fi

grep -E '^SIGNAL_REQUIRE_STRONG=|^SIGNAL_MIN_SCORE=|^SIGNAL_MIN_ADX=|^TP_MULTIPLIERS=' .env

docker compose up -d --build worker
docker compose exec -T worker python -c '
from app.core.config import get_settings
s = get_settings()
print("require_strong", s.signal_require_strong)
print("min_score", s.signal_min_score)
print("min_adx", s.signal_min_adx)
print("tp", s.parsed_tp_multipliers)
assert s.signal_require_strong is False
assert s.signal_min_score == 75.0
assert s.signal_min_adx == 30.0
print("OK")
'
