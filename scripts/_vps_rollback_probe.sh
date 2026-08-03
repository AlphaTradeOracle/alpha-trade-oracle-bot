#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
set -a; source .env; set +a

echo "=== HEAD ==="
git log -3 --format='%h %cI %s'

echo "=== TRENDLINE ENV ==="
grep -E '^SIGNAL_TRENDLINE_|^PAPER_RETEST' .env || true

echo "=== PAPER NOW ==="
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT id,name,ROUND(cash_balance::numeric,2) cash, ROUND(realized_pnl::numeric,2) realized FROM paper_accounts WHERE id=1;
   SELECT status, COUNT(*) FROM paper_positions WHERE account_id=1 GROUP BY 1 ORDER BY 1;"

echo "=== BACKUPS? ==="
ls -la exports/*paper* exports/*desk* exports/*snap* 2>/dev/null | head -30 || true
ls -la /var/backups 2>/dev/null | head || true
docker exec alpha-trade-oracle-postgres ls /var/lib/postgresql/data 2>/dev/null | head -5 || true

echo "=== WORKER AROUND 15:00 UTC ==="
docker logs alpha-trade-oracle-worker --since 2026-08-03T14:55:00Z --until 2026-08-03T15:35:00Z 2>&1 \
  | grep -E 'open_positions|retest_still_pending|scheduler_started|Paper-Rebuild|rebuild' | tail -30 || true

echo "=== SIMS ==="
pgrep -af 'top400|global_w' || echo none
docker ps --format '{{.Names}}' | grep worker-run || echo no_run
