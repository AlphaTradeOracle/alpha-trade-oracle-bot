SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
       COUNT(*) FILTER (WHERE in_universe AND is_active) AS active_universe,
       MIN(market_cap_rank) FILTER (WHERE in_universe) AS min_rank,
       MAX(market_cap_rank) FILTER (WHERE in_universe) AS max_rank
FROM assets;

-- Sample of universe symbols (top ranks)
SELECT symbol, market_cap_rank, exchange
FROM assets
WHERE in_universe
ORDER BY market_cap_rank NULLS LAST
LIMIT 15;
