#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
PG="docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -P pager=off"

echo "== universe =="
$PG -c "SELECT count(*) FILTER (WHERE in_universe) AS in_universe, count(*) FILTER (WHERE is_active) AS active, count(*) AS total_assets FROM assets;"

echo "== candles by timeframe (universe only) =="
$PG <<'SQL'
SELECT
  c.timeframe,
  count(*) AS candles,
  count(DISTINCT c.asset_id) AS assets,
  min(c.open_time) AS oldest,
  max(c.open_time) AS newest,
  round(extract(epoch from (max(c.open_time) - min(c.open_time))) / 86400.0, 1) AS span_days
FROM market_candles c
JOIN assets a ON a.id = c.asset_id
WHERE a.in_universe = true
GROUP BY c.timeframe
ORDER BY c.timeframe;
SQL

echo "== assets missing any TF (universe) =="
$PG <<'SQL'
WITH tfs AS (
  SELECT unnest(ARRAY['15m','1h','4h','1d']) AS timeframe
),
cov AS (
  SELECT a.symbol, a.market_cap_rank, c.timeframe, count(*) AS n,
         min(c.open_time) AS oldest, max(c.open_time) AS newest
  FROM assets a
  LEFT JOIN market_candles c ON c.asset_id = a.id
  WHERE a.in_universe = true
  GROUP BY a.symbol, a.market_cap_rank, c.timeframe
)
SELECT timeframe,
       count(*) FILTER (WHERE n IS NULL OR n = 0) AS missing_assets
FROM tfs
LEFT JOIN cov USING (timeframe)
GROUP BY timeframe
ORDER BY timeframe;
SQL

echo "== per-TF coverage depth (universe assets with data) =="
$PG <<'SQL'
SELECT
  c.timeframe,
  count(DISTINCT c.asset_id) AS assets_with_data,
  percentile_cont(0.5) WITHIN GROUP (
    ORDER BY extract(epoch from (max_ot - min_ot)) / 86400.0
  ) AS median_span_days,
  percentile_cont(0.1) WITHIN GROUP (
    ORDER BY extract(epoch from (max_ot - min_ot)) / 86400.0
  ) AS p10_span_days,
  min(extract(epoch from (max_ot - min_ot)) / 86400.0)::numeric(10,1) AS min_span_days,
  max(extract(epoch from (max_ot - min_ot)) / 86400.0)::numeric(10,1) AS max_span_days
FROM (
  SELECT asset_id, timeframe, min(open_time) AS min_ot, max(open_time) AS max_ot
  FROM market_candles
  GROUP BY asset_id, timeframe
) c
JOIN assets a ON a.id = c.asset_id AND a.in_universe = true
GROUP BY c.timeframe
ORDER BY c.timeframe;
SQL

echo "== newest candle age (universe) =="
$PG <<'SQL'
SELECT
  c.timeframe,
  max(c.open_time) AS newest_open,
  round(extract(epoch from (now() - max(c.open_time))) / 60.0, 1) AS minutes_behind
FROM market_candles c
JOIN assets a ON a.id = c.asset_id AND a.in_universe = true
GROUP BY c.timeframe
ORDER BY c.timeframe;
SQL
