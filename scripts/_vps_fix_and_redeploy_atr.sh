#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

pkill -f '_vps_deploy_atr_zone_rebuild' 2>/dev/null || true
pkill -f 'app.cli paper rebuild' 2>/dev/null || true
sleep 1

git checkout -- app/backtesting/engine.py scripts/run_top400_paper_parity_90d.py 2>/dev/null || true
rm -f scripts/_vps_deploy_atr_zone_rebuild.sh
git fetch origin
git reset --hard origin/main
echo "HEAD=$(git rev-parse --short HEAD)"

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s/^${key}=.*/${key}=${val}/" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
set_env ATR_MULTIPLIER 1.8
set_env PAPER_RETEST_ZONE_NEAR 0.40
set_env PAPER_RETEST_ZONE_FAR 1.15
grep -E '^(ATR_MULTIPLIER|PAPER_RETEST_ZONE_NEAR|PAPER_RETEST_ZONE_FAR)=' .env

# Patch deploy script: skip git pull (already hard-reset) to avoid race
# Run remaining steps inline.
LOG=/tmp/deploy_atr_zone_rebuild.log
SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
: >"$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy ATR/zone + paper rebuild =====" | tee -a "$LOG"
echo "HEAD=$(git rev-parse --short HEAD)" | tee -a "$LOG"
grep -E '^(ATR_MULTIPLIER|PAPER_RETEST_ZONE_NEAR|PAPER_RETEST_ZONE_FAR)=' .env | tee -a "$LOG"

echo "==> recreate worker/app" | tee -a "$LOG"
docker compose up -d --build --force-recreate --no-deps worker app 2>&1 | tee -a "$LOG"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok | tee -a "$LOG"
    break
  fi
  sleep 2
done

echo "==> live settings check" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
assert s.atr_multiplier == 1.8, s.atr_multiplier
assert s.paper_retest_zone_near == 0.40, s.paper_retest_zone_near
assert s.paper_retest_zone_far == 1.15, s.paper_retest_zone_far
print({
    "atr": s.atr_multiplier,
    "zone_near": s.paper_retest_zone_near,
    "zone_far": s.paper_retest_zone_far,
    "short_max": s.signal_short_max_score,
    "short_min": s.signal_short_min_score,
    "long_min": s.signal_min_score,
    "retest": s.paper_retest_entry_enabled,
    "max_open": s.paper_max_open_positions,
})
PY

echo "==> sync strategy_versions.atr_multiplier" | tee -a "$LOG"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "UPDATE strategy_versions SET atr_multiplier = 1.8 WHERE atr_multiplier IS DISTINCT FROM 1.8;" \
  2>&1 | tee -a "$LOG"

echo "==> paper rebuild since ${SINCE}" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" \
  --all-signals \
  --all-qualifying \
  2>&1 | tee -a "$LOG"

echo "==> desk snapshot" | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print({
  "equity": p.get("equity"),
  "cash": p.get("cash"),
  "realizedPnl": p.get("realizedPnl"),
  "closedTrades": p.get("closedTrades"),
  "openPositions": p.get("openPositions"),
  "pendingOrders": p.get("pendingOrders"),
  "winRatePct": p.get("winRatePct"),
  "totalReturnPct": p.get("totalReturnPct"),
})
' | tee -a "$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy ATR/zone + paper rebuild done =====" | tee -a "$LOG"
