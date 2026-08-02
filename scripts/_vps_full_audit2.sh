#!/usr/bin/env bash
# Continuation of full audit (fixed table names).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) AUDIT2 ====="

echo "=== SCAN JOB LIVE ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  job_key,
  last_status,
  run_count,
  last_run_at,
  last_success_at,
  next_run_at,
  NOW() AS now_utc,
  ROUND(EXTRACT(EPOCH FROM (NOW() - last_run_at)) / 60.0, 1) AS mins_since_last_run,
  LEFT(COALESCE(last_error, ''), 200) AS last_error
FROM scheduled_jobs
WHERE job_key ILIKE '%scan%';
SQL

echo
echo "=== SCAN EVENTS (application_events, 6h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  created_at,
  payload->>'symbols_scanned' AS scanned,
  payload->>'signals_created' AS created,
  payload->>'signals_dispatched' AS dispatched,
  payload->>'signals_suppressed' AS suppressed,
  payload->>'failures' AS failures,
  payload->>'universe_mode' AS universe
FROM application_events
WHERE event_type = 'market_scan_completed'
  AND created_at >= NOW() - INTERVAL '6 hours'
ORDER BY created_at DESC
LIMIT 24;
SQL

echo
echo "=== SCAN GAP (6h) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
WITH ev AS (
  SELECT created_at,
         LAG(created_at) OVER (ORDER BY created_at) AS prev_at
  FROM application_events
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
echo "=== UNIVERSE ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  COUNT(*) AS assets_total,
  COUNT(*) FILTER (WHERE in_universe) AS in_universe,
  COUNT(*) FILTER (WHERE in_universe AND last_scanned_at >= NOW() - INTERVAL '1 hour') AS scanned_1h,
  COUNT(*) FILTER (WHERE in_universe AND last_scanned_at >= NOW() - INTERVAL '24 hours') AS scanned_24h,
  COUNT(*) FILTER (WHERE in_universe AND (last_scanned_at IS NULL OR last_scanned_at < NOW() - INTERVAL '2 hours')) AS stale_2h
FROM assets;
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
echo "=== DIRECTION MIX 24h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT direction::text, COUNT(*) AS n, ROUND(AVG(score)::numeric,1) AS avg_score,
       COUNT(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY n DESC;
SQL

echo
echo "=== SUPPRESSION 24h ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT COALESCE(suppression_reason, '(none)') AS reason, COUNT(*) AS n
FROM signals
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY 1
ORDER BY n DESC
LIMIT 20;
SQL

echo
echo "=== ACTIONABLE / DISPATCHED 48h ==="
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
WHERE s.created_at >= NOW() - INTERVAL '48 hours'
  AND (
    s.is_dispatched
    OR s.score >= 75
    OR s.score <= 25
  )
ORDER BY s.created_at DESC
LIMIT 40;
SQL

echo
echo "=== NEAR-MISS LAST 6h (score 70-74 / 26-30) ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  s.created_at,
  a.symbol,
  s.direction,
  ROUND(s.score::numeric, 1) AS score,
  s.suppression_reason
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '6 hours'
  AND (
    (s.score >= 70 AND s.score < 75)
    OR (s.score > 25 AND s.score <= 30)
  )
ORDER BY s.created_at DESC
LIMIT 25;
SQL

echo
echo "=== PAPER ==="
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT status::text, COUNT(*) AS n FROM paper_trades GROUP BY 1 ORDER BY 1;
SELECT
  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '24 hours') AS opened_24h,
  COUNT(*) FILTER (WHERE closed_at >= NOW() - INTERVAL '24 hours') AS closed_24h,
  COUNT(*) FILTER (WHERE opened_at >= NOW() - INTERVAL '7 days') AS opened_7d
FROM paper_trades;
SQL

echo
echo "=== DESK SNAPSHOT KEYS ==="
curl -fsS --max-time 20 "http://127.0.0.1:8000/api/v1/desk/snapshot" -o /tmp/desk_snap.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk_snap.json"))
def dig(obj, *path):
    cur=obj
    for p in path:
        if not isinstance(cur, dict): return None
        cur=cur.get(p)
    return cur
# print compact tree of useful fields
for path in [
    ("portfolio","equity"),("portfolio","cash"),("portfolio","openPositions"),
    ("portfolio","pendingOrders"),("portfolio","closedTrades"),("portfolio","openUpnl"),
    ("portfolio","totalReturnPct"),("portfolio","winRatePct"),("portfolio","marginLocked"),
    ("portfolio","accountRealizedPnl"),("marketRegime","bias"),("marketRegime","globalScore"),
]:
    print(".".join(path), "=", dig(d, *path))
print("keys", list(d.keys())[:40])
PY

echo
echo "=== TOP COINS ==="
curl -fsS --max-time 15 "http://127.0.0.1:8000/api/v1/desk/top-coins?limit=12" -o /tmp/top_coins.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/top_coins.json"))
coins=d.get("coins") or []
syms=[c.get("symbol") for c in coins]
print("n", len(coins), "syms", " ".join(syms))
assert "USDT" not in syms and "USDC" not in syms
assert len(coins) >= 10
assert all(c.get("priceUsd") for c in coins)
print("TOP_COINS_OK")
PY

echo
echo "=== SITE ==="
curl -fsS -o /dev/null -w "site=%{http_code} health=%{http_code}\n" https://alpha-trade-oracle.com/
curl -fsS -o /dev/null -w "api_health=%{http_code}\n" http://127.0.0.1:8000/health || true

echo
echo "=== WORKER SCAN LOG 2h ==="
docker compose logs --since 2h worker 2>/dev/null \
  | grep -iE 'scan_started|scan_completed|job_started.*market_scan|job_failed|job_succeeded|scan_symbol_error|market_regime_resolved' \
  | tail -60 || true

echo
echo "=== WORKER ERRORS 2h ==="
docker compose logs --since 2h worker 2>/dev/null \
  | grep -iE 'ERROR|Traceback|Exception|invalid transaction' \
  | grep -viE 'HTTP Request|httpx' \
  | tail -40 || true

echo "===== AUDIT2 DONE ====="
