#!/usr/bin/env bash
# Fast recover: disable ranking, caps 40/24, docker-cp code, rebuild paper (no image build).
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/recover_fifo_rebuild.log
SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
: >"$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) recover FIFO rebuild =====" | tee -a "$LOG"
git fetch origin
git reset --hard origin/main | tee -a "$LOG"
echo "HEAD=$(git rev-parse --short HEAD)" | tee -a "$LOG"

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s/^${key}=.*/${key}=${val}/" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
set_env PAPER_MAX_OPEN_POSITIONS 40
set_env PAPER_MAX_OPEN_PER_DIRECTION 24
set_env PAPER_REBUILD_RANK_BY_SIM_PNL false
set_env ATR_MULTIPLIER 1.8
set_env PAPER_RETEST_ZONE_NEAR 0.40
set_env PAPER_RETEST_ZONE_FAR 1.15
grep -E '^(PAPER_MAX_OPEN|PAPER_REBUILD|ATR_MULTIPLIER|PAPER_RETEST_ZONE)=' .env | tee -a "$LOG"

CID=$(docker compose ps -q worker)
docker cp app/services/paper_trading_service.py "$CID:/app/app/services/paper_trading_service.py"
docker cp app/core/config.py "$CID:/app/app/core/config.py"
docker cp app/repositories/paper_repository.py "$CID:/app/app/repositories/paper_repository.py"

docker compose up -d --force-recreate --no-deps worker app 2>&1 | tee -a "$LOG"
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok | tee -a "$LOG"
    break
  fi
  sleep 2
done

docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
print({"max_open": s.paper_max_open_positions, "max_per_dir": s.paper_max_open_per_direction,
       "rank": s.paper_rebuild_rank_by_sim_pnl, "atr": s.atr_multiplier})
assert s.paper_rebuild_rank_by_sim_pnl is False
assert s.paper_max_open_positions == 40
PY

echo "==> paper rebuild" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" --all-signals --all-qualifying 2>&1 | tee -a "$LOG"

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys,json
p=json.load(sys.stdin).get("portfolio") or {}
print({k:p.get(k) for k in ["equity","closedTrades","winRatePct","totalReturnPct","openPositions","pendingOrders"]})
' | tee -a "$LOG"
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) recover done =====" | tee -a "$LOG"
