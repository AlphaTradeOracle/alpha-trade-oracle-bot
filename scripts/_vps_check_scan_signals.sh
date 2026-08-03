#!/bin/bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== WORKER / SCHEDULER ==="
docker compose ps worker app 2>/dev/null || docker compose ps
echo
grep -E 'SCAN_|UNIVERSE_|PAPER_|SCHED' .env 2>/dev/null | sed 's/=.*/=***/' || true
# show actual scan-related env without secrets
docker compose exec -T worker printenv | grep -E '^(SCAN_|UNIVERSE_|ENABLE_|PAPER_HOURLY)' | sort || true

echo
echo "=== RECENT SCAN JOBS ==="
docker compose exec -T postgres psql -U alpha -d alpha_trade_oracle -c "
SELECT id, job_name, status,
       started_at AT TIME ZONE 'UTC' AS started_utc,
       finished_at AT TIME ZONE 'UTC' AS finished_utc,
       EXTRACT(EPOCH FROM (COALESCE(finished_at, NOW()) - started_at))::int AS secs,
       LEFT(COALESCE(error,''), 120) AS err
FROM scan_jobs
ORDER BY id DESC
LIMIT 20;
"

echo "=== SCAN CADENCE (last 12h) ==="
docker compose exec -T postgres psql -U alpha -d alpha_trade_oracle -c "
SELECT date_trunc('hour', started_at) AS hour_utc,
       COUNT(*) AS jobs,
       COUNT(*) FILTER (WHERE status='completed' OR status='success' OR status='ok') AS okish,
       COUNT(*) FILTER (WHERE status='failed' OR status='error') AS failed
FROM scan_jobs
WHERE started_at > NOW() - INTERVAL '12 hours'
GROUP BY 1
ORDER BY 1 DESC;
"

echo "=== SIGNALS 24h ==="
docker compose exec -T postgres psql -U alpha -d alpha_trade_oracle -c "
SELECT COUNT(*) AS signals_24h FROM signals WHERE created_at > NOW() - INTERVAL '24 hours';
SELECT direction, COUNT(*) AS n
FROM signals
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
SELECT s.id, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at > NOW() - INTERVAL '24 hours'
ORDER BY s.created_at DESC
LIMIT 25;
"

echo "=== DELIVERIES 24h ==="
docker compose exec -T postgres psql -U alpha -d alpha_trade_oracle -c "
SELECT status, COUNT(*)
FROM signal_deliveries
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
" 2>/dev/null || docker compose exec -T postgres psql -U alpha -d alpha_trade_oracle -c "
SELECT status, COUNT(*)
FROM signal_deliveries
WHERE delivered_at > NOW() - INTERVAL '24 hours' OR created_at > NOW() - INTERVAL '24 hours'
GROUP BY 1 ORDER BY 2 DESC;
" 2>/dev/null || echo "(deliveries query skipped)"

echo "=== WORKER LOG TAIL ==="
docker compose logs worker --tail 100 2>&1 | tail -100
