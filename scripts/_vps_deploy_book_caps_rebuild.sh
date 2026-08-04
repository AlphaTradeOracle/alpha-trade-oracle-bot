#!/usr/bin/env bash
# Deploy max_open=16 + regime-aware dir caps (16 / neutral 8) + cash≥margin, rebuild paper.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/book_caps_deploy
SINCE="${PAPER_REBUILD_SINCE:-2026-07-31T16:32:35+00:00}"
LOG=/tmp/deploy_book_caps_rebuild.log
: >"$LOG"
cd "$APP"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

set_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then sed -i "s|^${key}=.*|${key}=${val}|" .env
  else echo "${key}=${val}" >> .env; fi
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy book caps =====" | tee -a "$LOG"

cp "$SRC/config.py" "$APP/app/core/config.py"
cp "$SRC/paper_trading_service.py" "$APP/app/services/paper_trading_service.py"

# Keep baseline geometry; apply new book rules
set_env ATR_MULTIPLIER 1.5
set_env PAPER_RETEST_ZONE_NEAR 0.55
set_env PAPER_RETEST_ZONE_FAR 1.0
set_env PAPER_MARGIN_PER_TRADE 300
set_env PAPER_MAX_PORTFOLIO_RISK_PCT 100
set_env PAPER_MAX_OPEN_POSITIONS 16
set_env PAPER_MAX_OPEN_PER_DIRECTION 16
set_env PAPER_MAX_OPEN_PER_DIRECTION_NEUTRAL 8
set_env PAPER_REBUILD_RANK_BY_SIM_PNL false
set_env REGIME_FILTER_ENABLED true
set_env MARKET_REGIME_HARD_VETO true

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
    "atr": s.atr_multiplier,
    "zone": (s.paper_retest_zone_near, s.paper_retest_zone_far),
    "margin": s.paper_margin_per_trade,
    "portfolio_risk_pct": s.paper_max_portfolio_risk_pct,
    "max_open": s.paper_max_open_positions,
    "max_per_dir": s.paper_max_open_per_direction,
    "max_per_dir_neutral": s.paper_max_open_per_direction_neutral,
    "regime_filter": s.regime_filter_enabled,
    "hard_veto": s.market_regime_hard_veto,
})
PY

echo "==> paper rebuild since $SINCE" | tee -a "$LOG"
dc exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" --all-signals --all-qualifying 2>&1 | tee -a "$LOG"

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_book_caps.json
python3 - <<'PY' | tee -a "$LOG"
import json
from datetime import datetime, timezone
from pathlib import Path

# desk
p = json.loads(Path("/tmp/desk_book_caps.json").read_text()).get("portfolio") or {}
print("desk", {k: p.get(k) for k in [
    "equity","closedTrades","winRatePct","totalReturnPct","openPositions","pendingOrders"
]})
PY

# peak concurrent
dc exec -T worker python - <<'PY' | tee -a "$LOG"
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
    events.sort(key=lambda x:(x[0], x[1]))
    cur=mx=0; peak_at=None
    for t,d in events:
        cur+=d
        if cur>mx:
            mx=cur; peak_at=t.isoformat()
    print({"filled_n": len(rows), "peak_concurrent_filled": mx, "peak_at": peak_at})

asyncio.run(main())
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy book caps done =====" | tee -a "$LOG"
