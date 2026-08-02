#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== market_scan events (90m) ==="
docker compose logs --since 90m worker 2>/dev/null \
  | grep -E 'market_scan_completed|market_scan_failed|invalid transaction|scheduled_job' \
  | tail -40 || true

echo
echo "=== job timing vs now ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT
  job_key,
  last_status,
  last_run_at,
  last_success_at,
  next_run_at,
  NOW() AS now_utc,
  ROUND(EXTRACT(EPOCH FROM (NOW() - COALESCE(last_success_at, last_run_at)))/60.0, 1) AS mins_since_success
FROM scheduled_jobs
WHERE is_enabled
ORDER BY job_key;
SQL
