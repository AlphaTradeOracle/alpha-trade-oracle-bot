SELECT symbol, market_cap_rank
FROM assets
WHERE in_universe = true
  AND is_active = true
  AND market_cap_rank IS NOT NULL
ORDER BY market_cap_rank
LIMIT 20;
