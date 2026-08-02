SELECT
  MIN(market_cap_rank) AS min_rank,
  MAX(market_cap_rank) AS max_rank,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY market_cap_rank) AS median_rank,
  PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY market_cap_rank) AS p90_rank,
  COUNT(*) FILTER (WHERE market_cap_rank <= 100) AS r1_100,
  COUNT(*) FILTER (WHERE market_cap_rank BETWEEN 101 AND 400) AS r101_400,
  COUNT(*) FILTER (WHERE market_cap_rank BETWEEN 401 AND 1000) AS r401_1000,
  COUNT(*) FILTER (WHERE market_cap_rank > 1000) AS r1001_plus,
  COUNT(*) FILTER (WHERE market_cap_rank IS NULL) AS rank_null
FROM assets
WHERE in_universe;

SELECT symbol, market_cap_rank, exchange
FROM assets
WHERE in_universe
ORDER BY market_cap_rank ASC NULLS LAST
LIMIT 10;

SELECT symbol, market_cap_rank, exchange
FROM assets
WHERE in_universe
ORDER BY market_cap_rank DESC NULLS LAST
LIMIT 10;
