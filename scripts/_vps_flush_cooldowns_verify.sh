#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

echo "=== flush all signal cooldowns ==="
docker compose exec -T redis sh -c 'redis-cli KEYS "signal:cooldown:*" | xargs -r redis-cli DEL' || true
echo "left=$(docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | wc -l)"

echo "=== force paper update on opens ==="
docker compose exec -T worker python - <<'PY'
import asyncio
from app.container import build_container
from app.database.session import session_scope
from app.scheduler.jobs import _collect_prices
from app.repositories.paper_repository import PaperRepository

async def main():
    c = build_container()
    async with session_scope() as session:
        acct = await c.paper_trading.get_or_create_account(session)
        repo = PaperRepository(session)
        opens = await repo.list_open_positions(acct.id)
        pendings = await repo.list_pending_positions(acct.id)
        print("open", [(p.symbol, str(p.opened_at), p.direction) for p in opens])
        print("pending", [(p.symbol, str(p.opened_at)) for p in pendings])
        if c.paper_trading.retest_enabled:
            r = await c.paper_trading.resolve_pending_retest(session, c.paper_price_provider)
            print("retest_resolve filled", getattr(r, "filled", r), "skipped", getattr(r, "skipped", None))
        symbols = [p.symbol for p in await repo.list_open_positions(acct.id)]
        if symbols:
            prices = await _collect_prices(c.paper_price_provider, symbols, providers=None)
            print("prices", prices)
            await c.paper_trading.update_open_positions(session, prices)
        opens2 = await repo.list_open_positions(acct.id)
        summary = await c.paper_trading.summary(session)
        print("open_after", len(opens2), [p.symbol for p in opens2])
        print(
            "equity", float(summary.equity),
            "cash", float(summary.cash_balance),
            "realized", float(summary.realized_pnl),
            "closed", summary.closed_trades,
        )

asyncio.run(main())
PY

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;
SELECT round(cash_balance::numeric,2) AS cash, round(realized_pnl::numeric,2) AS realized
FROM paper_accounts WHERE name='default';
SELECT symbol, status, opened_at, expires_at FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status IN ('open','pending') ORDER BY opened_at;
SELECT min(opened_at) AS earliest_closed_open FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status IN ('closed','open','pending');
SQL

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_aug1.json
python3 - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('/tmp/desk_aug1.json').read_text()).get('portfolio') or {}
print('DESK', {k:p.get(k) for k in ['equity','cash','realizedPnl','accountRealizedPnl','closedTrades','openPositions','pendingOrders','winRatePct','totalReturnPct']})
PY
