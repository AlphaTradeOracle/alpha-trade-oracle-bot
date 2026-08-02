#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) AUDIT4 ====="

echo "=== PAPER TABLES ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c "\dt *paper*"

echo
echo "=== PAPER COUNTS ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name ILIKE '%paper%'
ORDER BY 1;
SQL

# try common names
for t in paper_positions paper_trades paper_orders paper_accounts; do
  echo "-- $t"
  docker compose exec -T postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
    "SELECT COUNT(*) AS n FROM $t;" 2>/dev/null || true
done

echo
echo "=== ACTIONABLE DIRECTIONS LAST 6h (not NO_TRADE/NEUTRAL) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score, s.is_dispatched
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '6 hours'
  AND s.direction::text IN ('LONG','STRONG_LONG','SHORT','STRONG_SHORT')
ORDER BY s.created_at DESC
LIMIT 40;
SQL

echo
echo "=== HIGH SCORE BUT NO_TRADE (6h) — gate issue candidates ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT COUNT(*) AS high_score_no_trade
FROM signals
WHERE created_at >= NOW() - INTERVAL '6 hours'
  AND score >= 75
  AND direction::text = 'NO_TRADE';

SELECT COUNT(*) AS low_score_no_trade
FROM signals
WHERE created_at >= NOW() - INTERVAL '6 hours'
  AND score <= 25
  AND direction::text = 'NO_TRADE';

SELECT COUNT(*) AS short_actionable
FROM signals
WHERE created_at >= NOW() - INTERVAL '6 hours'
  AND direction::text IN ('SHORT','STRONG_SHORT')
  AND score <= 25;
SQL

echo
echo "=== WAIT FOR CURRENT SCAN COMPLETE (up to 10 min) ==="
for i in $(seq 1 40); do
  st=$(docker compose exec -T postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -Atc \
    "SELECT last_status||'|'||COALESCE(to_char(last_success_at,'HH24:MI:SS'),'') FROM scheduled_jobs WHERE job_key='market_scan:15m';")
  echo "t=${i} status=$st"
  case "$st" in
    success*|failed*) break ;;
  esac
  sleep 15
done

echo
echo "=== LATEST SCAN EVENT ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT created_at, payload
FROM application_events
WHERE event_type='market_scan_completed'
ORDER BY created_at DESC
LIMIT 2;
SQL

echo
echo "=== DESK PORTFOLIO ==="
python3 - <<'PY'
import json,urllib.request
d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/desk/snapshot", timeout=25))
p=d.get("portfolio") or {}
for k in sorted(p.keys()):
    print(f"{k}={p[k]}")
mr=d.get("marketRegime") or {}
print("regime.bias", mr.get("bias"), "hardVeto", mr.get("hardVeto"), "globalScore", mr.get("globalScore"))
PY

echo "===== AUDIT4 DONE ====="
