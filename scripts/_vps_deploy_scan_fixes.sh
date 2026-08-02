#!/usr/bin/env bash
# Deploy scan cadence + no_trade_reason fixes; migrate; restart worker/app; smoke.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy scan fixes ====="
git fetch origin main
git reset --hard origin/main
echo "bot=$(git rev-parse --short HEAD)"

# Ensure concurrency is set (idempotent)
if grep -q '^SCAN_CONCURRENCY=' .env; then
  sed -i 's/^SCAN_CONCURRENCY=.*/SCAN_CONCURRENCY=10/' .env
else
  echo 'SCAN_CONCURRENCY=10' >> .env
fi
if grep -q '^SIGNAL_MIN_ADX_SOFT=' .env; then
  sed -i 's/^SIGNAL_MIN_ADX_SOFT=.*/SIGNAL_MIN_ADX_SOFT=20/' .env
else
  echo 'SIGNAL_MIN_ADX_SOFT=20' >> .env
fi
grep -E '^(SCAN_CONCURRENCY|SIGNAL_MIN_ADX|SIGNAL_MIN_ADX_SOFT|SCAN_INTERVAL)' .env | sort

docker compose build app worker
docker compose up -d --no-deps app worker
sleep 4

docker compose exec -T app alembic upgrade head

echo "=== WAIT for a scan cycle start/finish (up to 20 min) ==="
# Trigger by checking job status; natural next run may be soon
for i in $(seq 1 80); do
  st=$(docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -Atc \
    "SELECT last_status||'|'||COALESCE(to_char(last_run_at,'HH24:MI:SS'),'')||'|'||COALESCE(to_char(last_success_at,'HH24:MI:SS'),'')||'|'||COALESCE((payload->>'duration_seconds'),'') FROM scheduled_jobs sj LEFT JOIN LATERAL (SELECT payload FROM application_events WHERE event_type='market_scan_completed' ORDER BY created_at DESC LIMIT 1) e ON true WHERE job_key='market_scan:15m';" 2>/dev/null || echo "?")
  echo "t=$i $st"
  # After deploy, wait until we see a completed event with duration_seconds
  dur=$(docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -Atc \
    "SELECT payload->>'duration_seconds' FROM application_events WHERE event_type='market_scan_completed' AND payload ? 'duration_seconds' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null || true)
  if [[ -n "${dur:-}" ]]; then
    echo "FOUND_DURATION=$dur"
    break
  fi
  sleep 15
done

echo "=== LATEST SCAN EVENT ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT created_at, payload
FROM application_events
WHERE event_type='market_scan_completed'
ORDER BY created_at DESC
LIMIT 2;

SELECT job_key, last_status, last_run_at, last_success_at, next_run_at,
       ROUND(EXTRACT(EPOCH FROM (last_success_at - last_run_at))/60.0, 1) AS duration_min
FROM scheduled_jobs WHERE job_key='market_scan:15m';

SELECT column_name FROM information_schema.columns
WHERE table_name='signals' AND column_name='no_trade_reason';
SQL

echo "===== DONE ====="
