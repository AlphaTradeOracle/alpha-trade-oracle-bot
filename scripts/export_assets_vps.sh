#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\pset format unaligned
\pset fieldsep '|'
\pset tuples_only off
SELECT
  a.id,
  a.symbol,
  a.base_asset,
  a.quote_asset,
  a.exchange,
  a.price_precision,
  a.quantity_precision,
  a.is_active,
  a.coingecko_id,
  a.market_cap_rank,
  a.market_cap_usd,
  a.in_universe,
  a.last_ranked_at,
  a.last_scanned_at,
  a.created_at,
  a.updated_at,
  COALESCE(c.candle_count, 0) AS candle_count,
  COALESCE(i.snapshot_count, 0) AS snapshot_count,
  COALESCE(s.signal_count, 0) AS signal_count
FROM assets a
LEFT JOIN (
  SELECT asset_id, COUNT(1) AS candle_count FROM market_candles GROUP BY asset_id
) c ON c.asset_id = a.id
LEFT JOIN (
  SELECT asset_id, COUNT(1) AS snapshot_count FROM indicator_snapshots GROUP BY asset_id
) i ON i.asset_id = a.id
LEFT JOIN (
  SELECT asset_id, COUNT(1) AS signal_count FROM signals GROUP BY asset_id
) s ON s.asset_id = a.id
ORDER BY a.market_cap_rank NULLS LAST, a.symbol;
SQL
