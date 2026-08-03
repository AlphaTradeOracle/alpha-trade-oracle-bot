#!/usr/bin/env bash
# Paper rebuild as-if bot started 2026-08-01 00:00 UTC on current Top-400 + live gates.
set -eu
cd /opt/alpha-trade-oracle-bot
SINCE="2026-08-01T00:00:00+00:00"
SYMFILE=/tmp/top400_universe.txt
LOG=/tmp/paper_rebuild_aug1_top400.log
: >"$LOG"

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
                select(Asset.symbol, Asset.market_cap_rank)
                .where(
                    Asset.in_universe.is_(True),
                    Asset.is_active.is_(True),
                    Asset.market_cap_rank.is_not(None),
                )
                .order_by(Asset.market_cap_rank.asc())
                .limit(400)
            )
        ).all()
    path = "/tmp/top400_universe.txt"
    with open(path, "w", encoding="utf-8") as f:
        for sym, rank in rows:
            f.write(f"{sym}\n")
    print(f"wrote {len(rows)} symbols -> {path}")
    print("first10", [r[0] for r in rows[:10]])
    print("last5", [r[0] for r in rows[-5:]])

asyncio.run(main())
PY
docker compose cp worker:/tmp/top400_universe.txt "$SYMFILE"

echo "=== signal counts since Aug1 ===" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
import asyncio
from datetime import datetime, timezone
from sqlalchemy import text
from app.database.session import session_scope
from app.core.logging import configure_logging
configure_logging("ERROR", json_output=False)

async def main():
    since = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    async with session_scope() as session:
        n = (await session.execute(text(
            "SELECT COUNT(*) FROM signals WHERE created_at >= :s "
            "AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')"
        ), {"s": since})).scalar()
        print("actionable_signals_since_aug1", n)
asyncio.run(main())
PY

echo "=== live gates ===" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
from app.core.config import get_settings
from app.strategies.weights import DEFAULT_WEIGHTS
s = get_settings()
print({
    "short_max": s.signal_short_max_score,
    "short_min": s.signal_short_min_score,
    "long_min": s.signal_min_score,
    "retest": s.paper_retest_entry_enabled,
    "mtf": DEFAULT_WEIGHTS.multi_timeframe,
    "structure": DEFAULT_WEIGHTS.market_structure,
})
PY

echo "=== paper rebuild ===" | tee -a "$LOG"
# copy symbols into worker
docker compose cp "$SYMFILE" worker:/tmp/top400_universe.txt
docker compose exec -T worker python -m app.cli paper rebuild \
  --since "$SINCE" \
  --all-signals \
  --all-qualifying \
  --symbols-file /tmp/top400_universe.txt \
  2>&1 | tee -a "$LOG"

echo "=== desk snapshot ===" | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
p = json.load(sys.stdin).get("portfolio") or {}
keys = ["equity","cash","realizedPnl","openPositions","pendingOrders","closedTrades","winRatePct","totalReturnPct"]
print({k: p.get(k) for k in keys})
' | tee -a "$LOG"
