#!/bin/bash
set -eu
SP=/opt/venv/lib/python3.12/site-packages/app
cd /opt/alpha-trade-oracle-bot

for pair in \
  "signals/risk.py:/tmp/risk.py" \
  "backtesting/engine.py:/tmp/engine.py"
do
  dest="${pair%%:*}"
  src="${pair##*:}"
  docker cp "$src" "alpha-trade-oracle-worker:${SP}/${dest}"
  docker cp "$src" "alpha-trade-oracle-worker:/app/app/${dest}"
  if docker ps --format '{{.Names}}' | grep -qx alpha-trade-oracle-app; then
    docker cp "$src" "alpha-trade-oracle-app:${SP}/${dest}" 2>/dev/null || true
    docker cp "$src" "alpha-trade-oracle-app:/app/app/${dest}" 2>/dev/null || true
  fi
done

echo "Verify TP_MULTIPLIERS in worker:"
docker compose exec -T worker python -c 'from app.signals.risk import TP_MULTIPLIERS; print(TP_MULTIPLIERS)'

echo "Running paper rebuild since 2026-07-28 ..."
docker compose exec -T worker python -m app.cli paper rebuild --since 2026-07-28 --all-qualifying

echo "Restarting worker/app ..."
docker compose restart worker app
sleep 5
docker compose exec -T worker python -c 'from app.signals.risk import TP_MULTIPLIERS; print("live", TP_MULTIPLIERS)'

docker compose exec -T worker python - <<'PY'
import asyncio
from app.database.session import session_scope
from sqlalchemy import text

async def main():
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT symbol, status, take_profit_1, take_profit_2, take_profit_3,
                   realized_pnl, entry_price, stop_loss
            FROM paper_trades
            ORDER BY id DESC
            LIMIT 10
        """))).mappings().all()
        for r in rows:
            entry = float(r["entry_price"])
            stop = float(r["stop_loss"])
            dist = abs(entry - stop)
            tp1, tp2, tp3 = float(r["take_profit_1"]), float(r["take_profit_2"]), float(r["take_profit_3"])
            r1 = abs(tp1 - entry) / dist if dist else 0
            r2 = abs(tp2 - entry) / dist if dist else 0
            r3 = abs(tp3 - entry) / dist if dist else 0
            print(f"{r['symbol']:12} {r['status']:10} R~={r1:.1f}/{r2:.1f}/{r3:.1f} pnl={r['realized_pnl']}")
        acct = (await session.execute(text(
            "SELECT balance, realized_pnl FROM paper_accounts ORDER BY id LIMIT 1"
        ))).mappings().first()
        if acct:
            print(f"balance={acct['balance']} realized={acct['realized_pnl']}")

asyncio.run(main())
PY
