#!/usr/bin/env bash
# Deploy paper live-realism fixes (wick watermark, entry clip, slippage, funding).
# Does NOT reset the paper ledger.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/live_realism_deploy
LOG=/tmp/deploy_live_realism.log
: >"$LOG"
cd "$APP"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy live realism =====" | tee -a "$LOG"

cp "$SRC/config.py" "$APP/app/core/config.py"
cp "$SRC/paper_trading_service.py" "$APP/app/services/paper_trading_service.py"

set_env PAPER_SLIPPAGE_PERCENT 0.05
set_env PAPER_FUNDING_ENABLED true
set_env PAPER_FUNDING_INTERVAL_HOURS 8
set_env PAPER_FUNDING_RATE_DEFAULT 0.0001

dc up -d --force-recreate --no-deps worker app >/dev/null
sleep 8
for c in alpha-trade-oracle-worker alpha-trade-oracle-app; do
  docker cp "$APP/app/core/config.py" "$c:/app/app/core/config.py"
  docker cp "$APP/app/services/paper_trading_service.py" "$c:/app/app/services/paper_trading_service.py"
done
dc restart worker app >/dev/null
sleep 8

dc exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
print({
    "slippage": s.paper_slippage_percent,
    "funding": s.paper_funding_enabled,
    "funding_h": s.paper_funding_interval_hours,
    "funding_default": s.paper_funding_rate_default,
    "fee": s.paper_fee_percent,
    "max_open": s.paper_max_open_positions,
    "initial": s.paper_initial_balance,
})
assert s.paper_slippage_percent == 0.05
assert s.paper_funding_enabled is True
assert s.paper_funding_interval_hours == 8.0
print("SETTINGS_OK")
PY

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_realism.json
python3 - <<'PY' | tee -a "$LOG"
import json
from pathlib import Path
p=json.loads(Path("/tmp/desk_realism.json").read_text()).get("portfolio") or {}
print("desk", {k:p.get(k) for k in ["equity","cash","openPositions","pendingOrders","closedTrades"]})
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy live realism DONE =====" | tee -a "$LOG"
