#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
PG="docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle"

echo "== scheduled_jobs =="
$PG -c "SELECT job_key, is_enabled, interval_seconds/60 AS mins, last_status, last_run_at, last_success_at, next_run_at, run_count FROM scheduled_jobs ORDER BY job_key;"

echo "== assets =="
$PG -c "\d assets" | head -40
$PG -c "SELECT count(*) AS total, count(*) FILTER (WHERE is_active) AS active FROM assets;"

echo "== now =="
date -u
