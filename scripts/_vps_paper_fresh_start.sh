#!/usr/bin/env bash
# Wipe paper ledger, reset cash to $5000, clear cooldowns. No historical rebuild.
set -eu
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) PAPER FRESH START ====="
grep -E '^PAPER_INITIAL_BALANCE=' .env || true

pkill -f 'app.cli paper rebuild' 2>/dev/null || true
docker ps -q --filter name=worker-run | xargs -r docker rm -f || true

echo "=== paper reset ==="
docker compose run --rm --no-deps worker python -m app.cli paper reset

echo "=== clear signal cooldowns ==="
docker compose exec -T redis sh -c \
  'redis-cli --scan --pattern "signal:cooldown:*" | while read -r k; do redis-cli DEL "$k" >/dev/null; done'
echo "cooldowns_left=$(docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | wc -l)"

echo "=== verify ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT name,
       round(initial_balance::numeric,2) AS initial,
       round(cash_balance::numeric,2) AS cash,
       round(realized_pnl::numeric,2) AS realized
FROM paper_accounts WHERE name='default';
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;
SQL

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_fresh.json
python3 - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('/tmp/desk_fresh.json').read_text()).get('portfolio') or {}
print('DESK', {k:p.get(k) for k in [
    'equity','cash','realizedPnl','accountRealizedPnl','totalCapital',
    'closedTrades','openPositions','pendingOrders','winRatePct','totalReturnPct'
]})
PY
echo "===== DONE — live from next scan/fill only ====="
