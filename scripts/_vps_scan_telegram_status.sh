#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== ENV ====="
grep -E '^(UNIVERSE_|SCAN_|SIGNAL_|TELEGRAM_SIGNAL|ENABLE_|MARKET_REGIME|PAPER_)' .env | grep -vE 'TOKEN|PASSWORD|SECRET|KEY' || true

echo "===== RUNTIME ====="
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
keys = [
    "universe_scan_enabled",
    "universe_target_count",
    "universe_max_rank",
    "universe_scan_batch_size",
    "universe_refresh_hours",
    "scan_interval_minutes",
    "signal_timeframe",
    "timeframes",
    "telegram_signal_dispatch",
    "signal_min_score",
    "signal_short_max_score",
    "signal_short_min_score",
    "signal_require_strong",
    "signal_cooldown_minutes",
    "enable_paper_trading",
    "market_regime_enabled",
    "market_regime_hard_veto",
]
for k in keys:
    if hasattr(s, k):
        print(f"{k}={getattr(s, k)}")
# leverage related
for k in dir(s):
    if "lever" in k.lower() or "perpetual" in k.lower() or "futures" in k.lower():
        print(f"{k}={getattr(s, k)}")
PY

echo "===== SCHEDULED JOBS ====="
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT job_key, job_type, interval_seconds, last_run_at, last_success_at, next_run_at, last_status, is_enabled
   FROM scheduled_jobs ORDER BY job_key;"

echo "===== RECENT DISPATCH / SUPPRESSION ====="
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT
     COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '2 hours') AS signals_2h,
     COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '2 hours' AND is_dispatched) AS dispatched_2h,
     COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '15 minutes') AS signals_15m,
     COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '15 minutes' AND is_dispatched) AS dispatched_15m
   FROM signals;"

echo "===== WORKER LOG SCAN ====="
docker compose logs worker --since 45m 2>/dev/null | grep -iE 'scan_|universe|dispatch|suppressed|Application started|Conflict' | tail -40 || true
