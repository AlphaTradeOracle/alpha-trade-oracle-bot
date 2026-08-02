#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== INSTITUTIONAL / SIGNAL ENV ==="
grep -E '^(INSTITUTIONAL_|SIGNAL_MIN_ADX|SIGNAL_BLOCK|SOFT_|SCORE_BLEND|ENABLE_SCHEDULER|ENABLE_UNIVERSE|SCAN_INTERVAL|UNIVERSE_SCAN_BATCH|REGIME_|MARKET_REGIME|SIGNAL_MIN_SCORE|SIGNAL_SHORT_)' .env | sort

echo
echo "=== PAPER OPEN ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT column_name FROM information_schema.columns
WHERE table_name='paper_positions' ORDER BY ordinal_position;

SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;

SELECT pp.opened_at, a.symbol, pp.direction, pp.status,
       ROUND(pp.entry_price::numeric,6) AS entry,
       ROUND(COALESCE(pp.unrealized_pnl,0)::numeric,2) AS upnl
FROM paper_positions pp
JOIN assets a ON a.id = pp.asset_id
WHERE pp.status = 'open'
ORDER BY pp.opened_at DESC;
SQL

echo
echo "=== BAND HEALTH 6h ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
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
SQL

echo
echo "=== NO_TRADE REASONS sample (from signal.reasons / notes if any) ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c "\d signals" | head -80

echo DONE
