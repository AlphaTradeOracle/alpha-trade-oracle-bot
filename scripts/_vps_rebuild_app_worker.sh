#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/rebuild_app_worker.log
: >"$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) build start =====" | tee -a "$LOG"
git log -1 --oneline | tee -a "$LOG"

docker compose build app worker 2>&1 | tee -a "$LOG"
docker compose up -d --force-recreate --no-deps app worker 2>&1 | tee -a "$LOG"

for i in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "health_ok after ${i} tries" | tee -a "$LOG"
    break
  fi
  sleep 2
done

docker compose ps | tee -a "$LOG"

docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.strategies.weights import DEFAULT_WEIGHTS as w
print("DEFAULT", w.model_dump())
import asyncio
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import StrategyWeights
configure_logging("ERROR", json_output=False)

async def main():
    async with session_scope() as s:
        a = await StrategyRepository(s).get_active_version(DEFAULT_STRATEGY_NAME)
        aw = StrategyWeights.from_db_columns(a)
        print("ACTIVE_v", a.version, aw.model_dump())
        print("match", aw.model_dump() == w.model_dump())

asyncio.run(main())
PY

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 - <<'PY' | tee -a "$LOG"
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print({k: p.get(k) for k in ("equity", "closedTrades", "openPositions", "pendingOrders", "winRatePct")})
PY

docker compose logs worker --since 2m 2>&1 | grep -E 'scheduler_started|container_built|ERROR|Traceback' | tail -20 | tee -a "$LOG"
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) rebuild done =====" | tee -a "$LOG"
