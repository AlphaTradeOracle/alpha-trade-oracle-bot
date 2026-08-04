#!/usr/bin/env bash
# Deploy max_open=40 / max_per_dir=24 + as-of caps + slot priority, rebuild paper.
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/deploy_caps_quality_rebuild.log
SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
: >"$LOG"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy caps/quality + paper rebuild =====" | tee -a "$LOG"

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
set_env PAPER_MAX_OPEN_POSITIONS 60
set_env PAPER_MAX_OPEN_PER_DIRECTION 36
set_env PAPER_REBUILD_RANK_BY_SIM_PNL true
# Keep ATR/zone combo
set_env ATR_MULTIPLIER 1.8
set_env PAPER_RETEST_ZONE_NEAR 0.40
set_env PAPER_RETEST_ZONE_FAR 1.15
grep -E '^(PAPER_MAX_OPEN|PAPER_REBUILD|ATR_MULTIPLIER|PAPER_RETEST_ZONE)=' .env | tee -a "$LOG"

echo "==> recreate worker/app" | tee -a "$LOG"
docker compose up -d --build --force-recreate --no-deps worker app 2>&1 | tee -a "$LOG"
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok | tee -a "$LOG"
    break
  fi
  sleep 2
done

echo "==> live settings" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
s = get_settings()
print({
    "max_open": s.paper_max_open_positions,
    "max_per_dir": s.paper_max_open_per_direction,
    "rank_sim_pnl": s.paper_rebuild_rank_by_sim_pnl,
    "atr": s.atr_multiplier,
    "zone": (s.paper_retest_zone_near, s.paper_retest_zone_far),
})
assert s.paper_max_open_positions == 60
assert s.paper_max_open_per_direction == 36
assert s.paper_rebuild_rank_by_sim_pnl is True
PY

echo "==> paper rebuild since ${SINCE}" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" \
  --all-signals \
  --all-qualifying \
  2>&1 | tee -a "$LOG"

echo "==> desk + peak concurrency" | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
print({k: p.get(k) for k in [
  "equity","cash","realizedPnl","closedTrades","openPositions",
  "pendingOrders","winRatePct","totalReturnPct","profitFactor"
]})
' | tee -a "$LOG"

docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from app.database.session import session_scope
from app.models.paper import PaperAccount, PaperPosition
from app.core.time import ensure_utc

async def main():
    async with session_scope() as s:
        acct = (await s.execute(select(PaperAccount).where(PaperAccount.name=="default"))).scalar_one()
        rows = (await s.execute(select(PaperPosition).where(
            PaperPosition.account_id==acct.id,
            PaperPosition.status.in_(("closed","open")),
        ))).scalars().all()
    events=[]
    for p in rows:
        if not p.opened_at: continue
        a=ensure_utc(p.opened_at)
        b=ensure_utc(p.closed_at) if p.closed_at else datetime.now(timezone.utc)
        events.append((a,1)); events.append((b,-1))
    events.sort(key=lambda x:(x[0],x[1]))
    cur=mx=0
    for _,d in events:
        cur+=d; mx=max(mx,cur)
    print({"filled_n": len(rows), "peak_concurrent_filled": mx,
           "open_now": sum(1 for p in rows if p.status=="open")})

asyncio.run(main())
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy caps/quality done =====" | tee -a "$LOG"
