#!/usr/bin/env bash
# Scenario B: short_max=30, since Jul31 16:32, Top-400 leverage universe only.
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/restore_scenario_b.log
SYMFILE=/tmp/top400_universe.txt
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

echo "=== export top400 ===" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
import asyncio
from sqlalchemy import select
from app.database.session import session_scope
from app.models.market import Asset
from app.core.logging import configure_logging
configure_logging("ERROR", json_output=False)

async def main():
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Asset.symbol)
                .where(
                    Asset.in_universe.is_(True),
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc())
                .limit(400)
            )
        ).scalars().all()
    with open("/tmp/top400_universe.txt", "w", encoding="utf-8") as f:
        for s in rows:
            f.write(f"{s}\n")
    print(f"wrote {len(rows)} symbols")

asyncio.run(main())
PY
docker compose cp worker:/tmp/top400_universe.txt "$SYMFILE"
docker compose cp "$SYMFILE" worker:/tmp/top400_universe.txt

echo "=== paper rebuild Jul31 top400 ===" | tee -a "$LOG"
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "2026-07-31T16:32:35+00:00" \
  --all-signals \
  --all-qualifying \
  --symbols-file /tmp/top400_universe.txt \
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
