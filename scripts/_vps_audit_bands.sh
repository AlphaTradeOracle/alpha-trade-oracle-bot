#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT pp.opened_at, a.symbol, pp.direction, pp.status,
       ROUND(pp.entry_price::numeric,6) AS entry,
       ROUND(pp.realized_pnl::numeric,2) AS realized,
       ROUND(pp.signal_score::numeric,1) AS score
FROM paper_positions pp
JOIN assets a ON a.id = pp.asset_id
WHERE pp.status = 'open'
ORDER BY pp.opened_at DESC;

SELECT
  COUNT(*) FILTER (WHERE score >= 75 AND direction::text = 'NO_TRADE') AS hi_score_notrade,
  COUNT(*) FILTER (WHERE score >= 75 AND direction::text IN ('LONG','STRONG_LONG')) AS hi_score_long_ok,
  COUNT(*) FILTER (WHERE score <= 25 AND direction::text IN ('SHORT','STRONG_SHORT')) AS short_band_ok,
  COUNT(*) FILTER (WHERE score <= 25 AND direction::text = 'NO_TRADE') AS lo_score_notrade,
  COUNT(*) FILTER (WHERE direction::text IN ('LONG','STRONG_LONG')) AS longs_any,
  COUNT(*) FILTER (WHERE direction::text IN ('SHORT','STRONG_SHORT')) AS shorts_any,
  COUNT(*) FILTER (WHERE is_dispatched) AS dispatched
FROM signals
WHERE created_at >= NOW() - INTERVAL '6 hours';

SELECT s.created_at, a.symbol, s.direction, ROUND(s.score::numeric,1) AS score, s.no_trade_reason
FROM signals s JOIN assets a ON a.id=s.asset_id
WHERE s.created_at >= NOW() - INTERVAL '6 hours' AND s.score >= 75
ORDER BY s.score DESC LIMIT 15;
SQL
