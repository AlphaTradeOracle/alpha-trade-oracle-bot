#!/usr/bin/env bash
# Rebuild paper book: only trades from 2026-08-01 14:00 UTC onward.
set -eu
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-08-01T14:00:00+00:00}"

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) PAPER REBUILD since $SINCE ====="
echo "HEAD=$(git rev-parse --short HEAD)"

# Stop leftover rebuilds
pkill -f 'app.cli paper rebuild' 2>/dev/null || true
docker ps -q --filter name=worker-run | xargs -r docker rm -f || true

echo "=== before ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;
SELECT round(cash_balance::numeric,2) AS cash, round(realized_pnl::numeric,2) AS realized
FROM paper_accounts WHERE name='default';
SQL

echo "=== rebuild (all-signals, no allowlist) ==="
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  worker python -m app.cli paper rebuild \
    --since "$SINCE" \
    --all-signals \
  2>&1 | tee /tmp/paper_rebuild_aug1_14.log | tail -n 60

echo "=== clear signal cooldowns (fresh book) ==="
docker compose exec -T redis redis-cli --scan --pattern 'signal:cooldown:*' | while read -r k; do
  docker compose exec -T redis redis-cli DEL "$k" >/dev/null
done
echo "cooldown_keys_left=$(docker compose exec -T redis redis-cli KEYS 'signal:cooldown:*' | wc -l)"

echo "=== after ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;
SELECT round(cash_balance::numeric,2) AS cash,
       round(realized_pnl::numeric,2) AS realized,
       round(initial_balance::numeric,2) AS initial
FROM paper_accounts WHERE name='default';
SELECT min(opened_at) AS earliest_open, max(opened_at) AS latest_open
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status IN ('open','pending','closed');
SELECT symbol, status, direction, opened_at
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status IN ('open','pending')
ORDER BY opened_at;
SQL

echo "===== DONE ====="
