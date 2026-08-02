#!/usr/bin/env bash
# Diagnose market_scan scheduled job health on VPS.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) scan job check ====="

echo "=== ENV ==="
grep -E '^(SCAN_INTERVAL|UNIVERSE_TARGET|UNIVERSE_SCAN_BATCH|ENABLE_SCHEDULER|ENABLE_UNIVERSE)' .env | sort || true

echo
echo "=== SERVICES ==="
systemctl is-active alpha-trade-oracle-bot 2>/dev/null || true
systemctl is-active alpha-trade-oracle-worker 2>/dev/null || true
systemctl list-units --type=service --all 'alpha*' --no-pager 2>/dev/null || true
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Service}}' 2>/dev/null || docker compose ps

echo
echo "=== SCHEDULED JOBS (scan*) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  job_key,
  is_enabled,
  interval_seconds / 60 AS mins,
  last_status,
  run_count,
  last_run_at,
  last_success_at,
  next_run_at,
  LEFT(COALESCE(last_error, ''), 200) AS last_error
FROM scheduled_jobs
WHERE job_key ILIKE '%scan%' OR job_type ILIKE '%scan%'
ORDER BY job_key;
SQL

echo
echo "=== ALL ENABLED JOBS ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT job_key, is_enabled, last_status, last_run_at, next_run_at,
       LEFT(COALESCE(last_error, ''), 120) AS last_error
FROM scheduled_jobs
WHERE is_enabled = true
ORDER BY next_run_at NULLS LAST, job_key;
SQL

echo
echo "=== UNIVERSE ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe, COUNT(*) AS assets_total FROM assets;"

echo
echo "=== SIGNALS / HOUR (last 12h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  date_trunc('hour', created_at) AS hour_utc,
  COUNT(*) AS n,
  COUNT(*) FILTER (WHERE direction IN ('SHORT','STRONG_SHORT') AND score <= 25) AS short_band,
  COUNT(*) FILTER (WHERE direction IN ('LONG','STRONG_LONG') AND score >= 75) AS long_band,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at >= NOW() - INTERVAL '12 hours'
GROUP BY 1
ORDER BY 1 DESC;
SQL

echo
echo "=== LAST DISPATCHED (48h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '48 hours'
  AND s.is_dispatched = true
ORDER BY s.created_at DESC
LIMIT 15;
SQL

echo
echo "=== WORKER LOG (scan / errors, 6h) ==="
UNIT=""
for u in alpha-trade-oracle-worker alpha-oracle-worker alpha-trade-oracle-bot; do
  if systemctl list-unit-files "$u.service" >/dev/null 2>&1; then
    UNIT="$u"
    break
  fi
done
if [[ -n "$UNIT" ]]; then
  echo "unit=$UNIT"
  journalctl -u "$UNIT" --since '6 hours ago' --no-pager \
    | grep -iE 'market_scan|run_market_scan|scan_completed|scan_failed|invalid transaction|Can.t reconnect|ERROR|Traceback' \
    | tail -60 || true
else
  echo "no systemd unit found; trying docker logs"
  docker compose logs --since 6h worker 2>/dev/null | grep -iE 'market_scan|scan|ERROR|invalid transaction' | tail -60 || true
  docker compose logs --since 6h app 2>/dev/null | grep -iE 'market_scan|scan|ERROR|invalid transaction' | tail -40 || true
fi

echo "===== done ====="
