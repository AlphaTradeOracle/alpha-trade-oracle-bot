#!/usr/bin/env bash
# Deploy soft-blend Market Regime + Institutional KB (gates advisory).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "==> git pull"
git fetch origin main
git reset --hard origin/main

upsert_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

echo "==> soft-blend env"
upsert_env MARKET_REGIME_ENABLED true
upsert_env MARKET_REGIME_HARD_VETO true
upsert_env INSTITUTIONAL_KB_ENABLED true
upsert_env INSTITUTIONAL_ENFORCE_GATES false
upsert_env SIGNAL_SHORT_MAX_SCORE 25
upsert_env SIGNAL_SHORT_MIN_SCORE 18

echo "==> build + migrate + restart"
docker compose build migrate app worker
docker compose run --rm migrate
docker compose up -d --no-deps app worker

echo "==> verify settings"
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("market_regime_enabled", s.market_regime_enabled)
print("market_regime_hard_veto", s.market_regime_hard_veto)
print("institutional_kb_enabled", s.institutional_kb_enabled)
print("institutional_enforce_gates", s.institutional_enforce_gates)
print("signal_short_max_score", s.signal_short_max_score)
print("signal_short_min_score", s.signal_short_min_score)
PY

docker compose ps app worker
docker compose logs worker --tail 25
echo "==> soft-blend deploy done"
