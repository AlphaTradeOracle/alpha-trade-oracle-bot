SELECT
  COUNT(*) FILTER (WHERE market_cap_rank IS NOT NULL) AS ranked,
  COUNT(*) FILTER (WHERE market_cap_rank <= 300) AS rank_le_300,
  COUNT(*) FILTER (WHERE market_cap_rank <= 500) AS rank_le_500,
  COUNT(*) FILTER (WHERE market_cap_rank <= 1000) AS rank_le_1000,
  COUNT(*) FILTER (WHERE is_active) AS active,
  COUNT(*) FILTER (WHERE in_universe) AS in_universe,
  MIN(market_cap_rank) AS min_rank,
  MAX(market_cap_rank) AS max_rank
FROM assets;

SELECT market_cap_rank, symbol, in_universe
FROM assets
WHERE is_active AND market_cap_rank IS NOT NULL
ORDER BY market_cap_rank ASC
OFFSET 280
LIMIT 25;
