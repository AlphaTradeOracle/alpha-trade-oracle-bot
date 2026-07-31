#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "==> Digest env"
grep -E 'PAPER_HOURLY_DIGEST|PAPER_DIGEST_INTERVAL|ENABLE_PAPER' .env || true

echo "==> Scheduled job row"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT job_key, job_type, interval_seconds, is_enabled, last_run_at, next_run_at, last_status
   FROM scheduled_jobs WHERE job_key LIKE 'paper_digest%' ORDER BY job_key;"

echo "==> Worker job registration"
docker compose logs worker --tail 120 2>/dev/null | grep -E 'paper_digest' || true

echo "==> Send digest once"
docker compose exec -T worker python -m app.cli paper digest --send

echo "DONE"
