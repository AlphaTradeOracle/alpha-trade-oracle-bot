#!/usr/bin/env bash
# Restore Scenario A: short_max=30, paper rebuild since Jul31 16:32, all symbols.
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/restore_scenario_a.log
: >"$LOG"

echo "=== short_max 30 ===" | tee -a "$LOG"
sed -i 's/^SIGNAL_SHORT_MAX_SCORE=.*/SIGNAL_SHORT_MAX_SCORE=30/' .env
grep '^SIGNAL_SHORT_MAX_SCORE=' .env | tee -a "$LOG"

docker compose up -d --force-recreate --no-deps worker app
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok | tee -a "$LOG"
    break
  fi
  sleep 2
done

docker compose exec -T worker python -c 'from app.core.config import get_settings; print("live_short_max", get_settings().signal_short_max_score)' | tee -a "$LOG"

echo "=== paper rebuild Jul31 all ===" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "2026-07-31T16:32:35+00:00" \
  --all-signals \
  --all-qualifying \
  2>&1 | tee -a "$LOG"

echo "=== desk ===" | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print({
  "equity": p.get("equity"),
  "realizedPnl": p.get("realizedPnl"),
  "closedTrades": p.get("closedTrades"),
  "openPositions": p.get("openPositions"),
  "pendingOrders": p.get("pendingOrders"),
  "winRatePct": p.get("winRatePct"),
  "totalReturnPct": p.get("totalReturnPct"),
})
' | tee -a "$LOG"
