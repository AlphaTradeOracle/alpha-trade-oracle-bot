#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\pset border 2

\echo === 3 beste Long-Kandidaten 48h (höchster Score) + Suppress-Grund ===
SELECT a.symbol,
       s.direction,
       ROUND(s.score::numeric,2) AS score,
       s.confidence,
       ROUND(COALESCE(s.risk_reward_ratio,0)::numeric,2) AS rr,
       s.is_dispatched,
       LEFT(COALESCE(s.invalidation_note,''), 90) AS invalidation,
       LEFT(COALESCE(s.reasons::text,''), 120) AS reasons,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '48 hours'
  AND s.score >= 70
ORDER BY s.score DESC, s.created_at DESC
LIMIT 3;

\echo === 3 knapste Shorts an der Band (18-28) zuletzt, nicht dispatched ===
SELECT a.symbol,
       s.direction,
       ROUND(s.score::numeric,2) AS score,
       s.confidence,
       ROUND(COALESCE(s.risk_reward_ratio,0)::numeric,2) AS rr,
       s.is_dispatched,
       LEFT(COALESCE(s.invalidation_note,''), 90) AS invalidation,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s
JOIN assets a ON a.id = s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '24 hours'
  AND s.direction IN ('SHORT','STRONG_SHORT')
  AND s.score BETWEEN 18 AND 28
  AND s.is_dispatched = false
ORDER BY
  ABS(s.score - 21.5),  -- Mitte der Short-Band
  s.created_at DESC
LIMIT 8;

\echo === 3 knapste unterhalb Long-Band (70-74.99) 24h ===
SELECT a.symbol, s.direction, ROUND(s.score::numeric,2) score, s.confidence,
       s.is_dispatched,
       LEFT(COALESCE(s.invalidation_note,''), 100) AS note,
       s.created_at AT TIME ZONE 'UTC' AS created_utc
FROM signals s JOIN assets a ON a.id=s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '24 hours'
  AND s.score BETWEEN 70 AND 74.99
ORDER BY s.score DESC
LIMIT 5;
SQL

# Worker suppress detail for top symbols if recent
echo
echo "=== worker suppress (PIEVERSE/ZEST/GUSDT/B3, last 12h) ==="
docker compose logs worker --since 12h 2>/dev/null \
  | grep -E 'PIEVERSEUSDT|ZESTUSDT|GUSDT|B3USDT|BARDUSDT' \
  | grep -E 'signal_suppressed|signal_dispatched' \
  | tail -20 || true
