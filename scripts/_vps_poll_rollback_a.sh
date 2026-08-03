#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
echo "=== HEAD $(git rev-parse --short HEAD) ==="
echo "=== rebuild log ==="
wc -l /tmp/paper_rebuild_rollback_a.log || true
tail -30 /tmp/paper_rebuild_rollback_a.log || true
echo "=== process ==="
pgrep -af 'app.cli paper rebuild' || echo REBUILD_DONE
echo "=== paper book ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, count(*)
FROM paper_positions
WHERE account_id = (SELECT id FROM paper_accounts WHERE name = 'default')
GROUP BY 1
ORDER BY 1;
SELECT round(equity::numeric,2) AS equity, round(cash::numeric,2) AS cash,
       round(realized_pnl::numeric,2) AS realized
FROM paper_accounts WHERE name = 'default';
SQL
