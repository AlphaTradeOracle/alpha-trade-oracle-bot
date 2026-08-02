#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) AUDIT3 ====="

echo "=== SUPPRESSION VIA DELIVERIES 24h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT COALESCE(suppression_reason, status::text, '(none)') AS reason, COUNT(*) AS n
FROM signal_deliveries
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY n DESC
LIMIT 25;
SQL

echo
echo "=== BAND SIGNALS LAST 6h (score<=25 or >=75) + latest delivery ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  s.created_at,
  a.symbol,
  s.direction,
  ROUND(s.score::numeric, 1) AS score,
  s.is_dispatched,
  (
    SELECT sd.suppression_reason
    FROM signal_deliveries sd
    WHERE sd.signal_id = s.id
    ORDER BY sd.created_at DESC
    LIMIT 1
  ) AS last_suppression,
  (
    SELECT sd.status::text
    FROM signal_deliveries sd
    WHERE sd.signal_id = s.id
    ORDER BY sd.created_at DESC
    LIMIT 1
  ) AS last_delivery_status
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '6 hours'
  AND (s.score >= 75 OR s.score <= 25)
ORDER BY s.created_at DESC
LIMIT 50;
SQL

echo
echo "=== DISPATCHED LAST 7d ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.is_dispatched
  AND s.created_at >= NOW() - INTERVAL '7 days'
ORDER BY s.created_at DESC
LIMIT 30;
SQL

echo
echo "=== REGIME / FILTER ENV VALUES ==="
grep -E '^(SIGNAL_MIN_SCORE|SIGNAL_SHORT_MAX_SCORE|SIGNAL_SHORT_MIN_SCORE|SIGNAL_REQUIRE_STRONG|REGIME_FILTER_ENABLED|MARKET_REGIME|SIGNAL_MIN_ADX|SIGNAL_BLOCK_RANGE|MIN_RISK_REWARD|UNIVERSE_SCAN_BATCH|SCAN_INTERVAL)' .env | sort

echo
echo "=== CURRENT REGIME FROM DESK ==="
python3 - <<'PY'
import json,urllib.request
d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/desk/snapshot", timeout=20))
mr=d.get("marketRegime") or {}
print(json.dumps(mr, indent=2)[:2000])
PY

echo
echo "=== SCAN STUCK? worker activity ==="
docker compose logs --since 30m worker 2>/dev/null | grep -iE 'scan_|job_|regime|symbol=' | tail -40

echo
echo "=== PAPER OPEN/PENDING ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT status::text, COUNT(*) FROM paper_trades GROUP BY 1 ORDER BY 1;

SELECT pt.opened_at, a.symbol, pt.side, pt.status, ROUND(pt.entry_price::numeric,6) AS entry
FROM paper_trades pt
JOIN assets a ON a.id = pt.asset_id
WHERE pt.status IN ('open','pending')
ORDER BY pt.opened_at DESC
LIMIT 20;
SQL

echo
echo "=== TOP COINS + HEALTH ==="
curl -fsS --max-time 15 "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/top_coins.json"))
coins=d.get("coins") or []
print(" ".join(c["symbol"] for c in coins))
print("TOP_OK", len(coins)==10 and "USDT" not in [c["symbol"] for c in coins])
PY
curl -fsS -o /dev/null -w "health=%{http_code} site=%{http_code}\n" http://127.0.0.1:8000/health
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/

echo
echo "=== JOB AFTER WAIT (status) ==="
sleep 5
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT job_key, last_status, last_run_at, last_success_at, next_run_at, ROUND(EXTRACT(EPOCH FROM (NOW()-last_run_at))/60.0,1) AS mins FROM scheduled_jobs WHERE is_enabled;"

echo "===== AUDIT3 DONE ====="
