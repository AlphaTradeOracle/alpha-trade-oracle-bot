#!/usr/bin/env bash
# Deploy scheduler fix, disable paper_digest, run overdue universe_refresh once.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) digest off + universe fix ====="

git fetch origin main
git reset --hard origin/main
echo "bot=$(git rev-parse --short HEAD)"

if grep -q '^PAPER_HOURLY_DIGEST_ENABLED=' .env; then
  sed -i 's/^PAPER_HOURLY_DIGEST_ENABLED=.*/PAPER_HOURLY_DIGEST_ENABLED=false/' .env
else
  echo 'PAPER_HOURLY_DIGEST_ENABLED=false' >> .env
fi

docker compose build worker
docker compose up -d --no-deps worker

echo
echo "=== disable digest + arm universe_refresh ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
UPDATE scheduled_jobs
SET is_enabled = false,
    last_status = 'disabled',
    last_error = 'disabled: desk website is the status surface',
    updated_at = NOW()
WHERE job_key LIKE 'paper_digest%';

UPDATE scheduled_jobs
SET is_enabled = true,
    next_run_at = NOW(),
    last_error = NULL,
    updated_at = NOW()
WHERE job_key = 'universe_refresh:24h';

SELECT job_key, is_enabled, last_status, next_run_at, last_success_at
FROM scheduled_jobs
WHERE job_key LIKE 'paper_digest%' OR job_key = 'universe_refresh:24h'
ORDER BY job_key;
SQL

echo
echo "=== config ==="
docker compose exec -T worker python -c \
  'from app.core.config import get_settings; s=get_settings(); print("digest", s.paper_hourly_digest_enabled); print("universe_scan", s.enable_universe_scan); print("refresh_h", s.universe_refresh_hours)'

echo
echo "=== CLI universe refresh (may take several minutes) ==="
set +e
docker compose exec -T worker python -m app.cli universe refresh
REFRESH_RC=$?
set -e
echo "universe_refresh_exit=$REFRESH_RC"

echo
echo "=== mark job success / next run ==="
if [[ "$REFRESH_RC" -eq 0 ]]; then
  docker compose exec -T postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
UPDATE scheduled_jobs
SET last_status = 'success',
    last_success_at = NOW(),
    last_run_at = NOW(),
    next_run_at = NOW() + (interval_seconds * INTERVAL '1 second'),
    last_error = NULL,
    run_count = run_count + 1,
    updated_at = NOW()
WHERE job_key = 'universe_refresh:24h';
SQL
fi

# Recreate worker so APScheduler picks DB next_run (+ drops digest).
docker compose up -d --no-deps --force-recreate worker
sleep 10

echo
echo "=== scheduler boot ==="
docker compose logs worker --since 30s 2>/dev/null \
  | grep -E 'scheduler_started|scheduler_jobs_disabled|job_started|universe_refresh' || true

echo
echo "=== final jobs ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT job_key, is_enabled, last_status, run_count,
       last_run_at, last_success_at, next_run_at,
       LEFT(COALESCE(last_error,''), 100) AS last_error
FROM scheduled_jobs
ORDER BY job_key;
SQL

echo "===== done rc=$REFRESH_RC ====="
exit "$REFRESH_RC"
