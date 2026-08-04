#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/deploy_atr_zone_rebuild2.log
SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
: >"$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) resume ATR deploy =====" | tee -a "$LOG"
git rev-parse --short HEAD | tee -a "$LOG"
grep -E '^(ATR_MULTIPLIER|PAPER_RETEST_ZONE)' .env | tee -a "$LOG"

echo "==> recreate worker/app" | tee -a "$LOG"
docker compose up -d --force-recreate --no-deps worker app 2>&1 | tee -a "$LOG"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok | tee -a "$LOG"
    break
  fi
  sleep 2
done
docker compose ps | tee -a "$LOG"

echo "==> live settings" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
print({
    "atr": s.atr_multiplier,
    "near": s.paper_retest_zone_near,
    "far": s.paper_retest_zone_far,
})
assert s.atr_multiplier == 1.8, s.atr_multiplier
assert s.paper_retest_zone_near == 0.40, s.paper_retest_zone_near
assert s.paper_retest_zone_far == 1.15, s.paper_retest_zone_far
PY

echo "==> paper rebuild since ${SINCE}" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" \
  --all-signals \
  --all-qualifying \
  2>&1 | tee -a "$LOG" | tee /tmp/paper_rebuild_atr.log

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

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) resume ATR deploy done =====" | tee -a "$LOG"
