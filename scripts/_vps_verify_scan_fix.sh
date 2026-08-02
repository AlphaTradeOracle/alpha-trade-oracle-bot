#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== BAND + NO_TRADE REASONS (since last scan start) ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT
  COUNT(*) FILTER (WHERE score >= 75 AND direction::text IN ('LONG','STRONG_LONG')) AS hi_long_ok,
  COUNT(*) FILTER (WHERE score >= 75 AND direction::text = 'NO_TRADE') AS hi_notrade,
  COUNT(*) FILTER (WHERE score <= 25 AND direction::text IN ('SHORT','STRONG_SHORT')) AS short_band_ok,
  COUNT(*) FILTER (WHERE score <= 25 AND direction::text = 'NO_TRADE') AS lo_notrade,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at >= '2026-08-02 19:07:57+00';

SELECT COALESCE(LEFT(no_trade_reason, 80), '(null)') AS reason, COUNT(*) AS n
FROM signals
WHERE created_at >= '2026-08-02 19:07:57+00'
  AND score >= 75
GROUP BY 1
ORDER BY n DESC
LIMIT 15;

SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score,
       LEFT(COALESCE(s.no_trade_reason,''), 70) AS reason
FROM signals s JOIN assets a ON a.id=s.asset_id
WHERE s.created_at >= '2026-08-02 19:07:57+00'
  AND s.score >= 75
ORDER BY s.score DESC
LIMIT 20;
SQL
