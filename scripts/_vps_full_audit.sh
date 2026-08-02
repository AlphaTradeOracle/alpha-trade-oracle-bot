#!/usr/bin/env bash
# Full desk + scanner audit (15m cadence, signals, paper, health).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) FULL AUDIT START ====="
echo "bot_commit=$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "dash_commit=$(git rev-parse --short origin/cursor/trading-dashboard-efe9 2>/dev/null || echo '?')"

echo
echo "=== DOCKER / SERVICES ==="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Service}}' 2>/dev/null || docker compose ps

echo
echo "=== CRITICAL ENV ==="
grep -E '^(SCAN_INTERVAL|UNIVERSE_|ENABLE_|SIGNAL_|REGIME_|PAPER_|TELEGRAM_|MARKET_REGIME)' .env 2>/dev/null | sed 's/=.*/=***/' | sort || true
# show non-secret scan-related values plainly
grep -E '^(SCAN_INTERVAL_MINUTES|UNIVERSE_TARGET_SIZE|UNIVERSE_SCAN_BATCH_SIZE|ENABLE_SCHEDULER|ENABLE_UNIVERSE_SCAN|ENABLE_PAPER_TRADING|SIGNAL_ENTRY_BLACKOUT|REGIME_FILTER_ENABLED|MARKET_REGIME_ENABLED|PAPER_STAKE|PAPER_NOTIONAL)' .env 2>/dev/null | sort || true

echo
echo "=== ALL SCHEDULED JOBS ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  job_key,
  is_enabled,
  interval_seconds / 60.0 AS mins,
  last_status,
  run_count,
  last_run_at,
  last_success_at,
  next_run_at,
  CASE
    WHEN next_run_at IS NULL THEN 'NO_NEXT'
    WHEN next_run_at < NOW() - INTERVAL '5 minutes' THEN 'OVERDUE'
    WHEN next_run_at < NOW() + INTERVAL '20 minutes' THEN 'DUE_SOON'
    ELSE 'OK'
  END AS due_state,
  LEFT(COALESCE(last_error, ''), 180) AS last_error
FROM scheduled_jobs
ORDER BY is_enabled DESC, job_key;
SQL

echo
echo "=== SCAN CADENCE (market_scan events, last 6h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
WITH ev AS (
  SELECT created_at, payload
  FROM system_events
  WHERE event_type = 'market_scan_completed'
    AND created_at >= NOW() - INTERVAL '6 hours'
  ORDER BY created_at DESC
)
SELECT
  created_at,
  payload->>'symbols_scanned' AS scanned,
  payload->>'signals_created' AS created,
  payload->>'signals_dispatched' AS dispatched,
  payload->>'signals_suppressed' AS suppressed,
  payload->>'failures' AS failures,
  payload->>'universe_mode' AS universe
FROM ev
ORDER BY created_at DESC
LIMIT 20;
SQL

echo
echo "=== SCAN GAP ANALYSIS (last 6h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
WITH ev AS (
  SELECT created_at,
         LAG(created_at) OVER (ORDER BY created_at) AS prev_at
  FROM system_events
  WHERE event_type = 'market_scan_completed'
    AND created_at >= NOW() - INTERVAL '6 hours'
)
SELECT
  COUNT(*) AS scan_events_6h,
  ROUND(AVG(EXTRACT(EPOCH FROM (created_at - prev_at))) / 60.0, 1) AS avg_gap_min,
  ROUND(MIN(EXTRACT(EPOCH FROM (created_at - prev_at))) / 60.0, 1) AS min_gap_min,
  ROUND(MAX(EXTRACT(EPOCH FROM (created_at - prev_at))) / 60.0, 1) AS max_gap_min,
  MAX(created_at) AS last_scan_at,
  ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(created_at))) / 60.0, 1) AS minutes_since_last
FROM ev
WHERE prev_at IS NOT NULL;
SQL

echo
echo "=== UNIVERSE / ASSETS ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  COUNT(*) AS assets_total,
  COUNT(*) FILTER (WHERE in_universe) AS in_universe,
  COUNT(*) FILTER (WHERE in_universe AND last_scanned_at >= NOW() - INTERVAL '1 hour') AS scanned_1h,
  COUNT(*) FILTER (WHERE in_universe AND last_scanned_at >= NOW() - INTERVAL '24 hours') AS scanned_24h,
  COUNT(*) FILTER (WHERE in_universe AND (last_scanned_at IS NULL OR last_scanned_at < NOW() - INTERVAL '2 hours')) AS stale_2h
FROM assets;

SELECT a.symbol, a.last_scanned_at, a.market_cap_rank
FROM assets a
WHERE a.in_universe
ORDER BY a.last_scanned_at NULLS FIRST
LIMIT 8;
SQL

echo
echo "=== SIGNALS LAST 24h BY HOUR ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  date_trunc('hour', created_at) AS hour_utc,
  COUNT(*) AS n,
  COUNT(*) FILTER (WHERE direction::text ILIKE '%LONG%') AS longs,
  COUNT(*) FILTER (WHERE direction::text ILIKE '%SHORT%') AS shorts,
  COUNT(*) FILTER (WHERE direction::text ILIKE '%NEUTRAL%' OR direction::text = 'NO_TRADE') AS neutralish,
  COUNT(*) FILTER (WHERE score >= 75) AS score_ge_75,
  COUNT(*) FILTER (WHERE score <= 25) AS score_le_25,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched,
  ROUND(AVG(score)::numeric, 1) AS avg_score
FROM signals
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY 1 DESC;
SQL

echo
echo "=== ACTIONABLE BAND LAST 24h (detail) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  s.created_at,
  a.symbol,
  s.direction,
  ROUND(s.score::numeric, 1) AS score,
  s.timeframe,
  s.is_dispatched,
  s.suppression_reason
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '24 hours'
  AND (
    s.is_dispatched
    OR s.score >= 75
    OR s.score <= 25
    OR s.direction::text IN ('LONG','STRONG_LONG','SHORT','STRONG_SHORT')
  )
ORDER BY s.created_at DESC
LIMIT 40;
SQL

echo
echo "=== SUPPRESSION BREAKDOWN 24h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT COALESCE(suppression_reason::text, '(none)') AS reason, COUNT(*) AS n
FROM signals
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY n DESC;
SQL

echo
echo "=== DIRECTION MIX 24h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT direction::text, COUNT(*) AS n, ROUND(AVG(score)::numeric,1) AS avg_score
FROM signals
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY n DESC;
SQL

echo
echo "=== PAPER TRADES SNAPSHOT ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT status, COUNT(*) AS n
FROM paper_trades
GROUP BY 1
ORDER BY 1;

SELECT
  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS opened_24h,
  COUNT(*) FILTER (WHERE closed_at >= NOW() - INTERVAL '24 hours') AS closed_24h
FROM paper_trades;
SQL

echo
echo "=== DESK API SNAPSHOT ==="
curl -fsS --max-time 15 "http://127.0.0.1:8000/api/v1/desk/snapshot" -o /tmp/desk_snap.json || echo "DESK_FAIL"
python3 - <<'PY'
import json, sys
try:
    d = json.load(open("/tmp/desk_snap.json"))
except Exception as e:
    print("no snapshot", e)
    sys.exit(0)
p = d.get("portfolio") or d
# tolerate nested shapes
keys = ["equity","cash","openPositions","pendingOrders","closedTrades","openUpnl","totalReturnPct","winRatePct","marginLocked"]
for k in keys:
    if k in d:
        print(f"{k}={d.get(k)}")
    elif isinstance(p, dict) and k in p:
        print(f"{k}={p.get(k)}")
print("top_keys", sorted(d.keys())[:30])
PY

echo
echo "=== TOP-COINS API ==="
curl -fsS --max-time 15 "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10" -o /tmp/top_coins.json || echo "TOP_FAIL"
python3 - <<'PY'
import json
d=json.load(open("/tmp/top_coins.json"))
coins=d.get("coins") or []
syms=[c.get("symbol") for c in coins]
print("n", len(coins), "syms", " ".join(syms))
print("has_USDT", "USDT" in syms, "has_USDC", "USDC" in syms)
print("all_have_price", all(c.get("priceUsd") for c in coins))
print("all_have_image", all(c.get("imageUrl") for c in coins))
PY

echo
echo "=== WORKER LOG (scan/errors, 3h) ==="
docker compose logs --since 3h worker 2>/dev/null \
  | grep -iE 'market_scan|scan_completed|scan_started|job_started|job_failed|scan_symbol_|invalid transaction|Traceback|ERROR|regime' \
  | tail -80 || true

echo
echo "=== APP LOG errors 3h ==="
docker compose logs --since 3h app 2>/dev/null \
  | grep -iE 'ERROR|Traceback|Exception' \
  | tail -40 || true

echo
echo "=== HEALTH ENDPOINTS ==="
curl -fsS -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:8000/health || echo "health=FAIL"
curl -fsS -o /dev/null -w "ready=%{http_code}\n" http://127.0.0.1:8000/ready || curl -fsS -o /dev/null -w "api_root=%{http_code}\n" http://127.0.0.1:8000/ || true
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/ || true

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) FULL AUDIT DONE ====="
