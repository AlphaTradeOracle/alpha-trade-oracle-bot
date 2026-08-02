SELECT
  COUNT(*) FILTER (WHERE in_universe) AS in_universe,
  COUNT(*) AS assets
FROM assets;
