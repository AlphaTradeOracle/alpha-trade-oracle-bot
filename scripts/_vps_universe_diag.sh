#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== ENV (universe-related) ==="
grep -E '^(UNIVERSE_|DEFAULT_QUOTE|PRIMARY_TIMEFRAME|PAPER_USE_PERP|PAPER_PERP_|MARKET_DATA)' .env || true

echo
echo "=== ASSETS COUNTS ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE in_universe) AS in_universe,
       COUNT(*) FILTER (WHERE in_universe AND is_active) AS in_uni_active,
       COUNT(*) FILTER (WHERE market_cap_rank IS NOT NULL) AS with_rank,
       COUNT(*) FILTER (WHERE in_universe AND market_cap_rank IS NULL) AS uni_null_rank,
       MIN(market_cap_rank) FILTER (WHERE in_universe) AS min_rank,
       MAX(market_cap_rank) FILTER (WHERE in_universe) AS max_rank
FROM assets;

SELECT exchange, COUNT(*) AS n
FROM assets WHERE in_universe
GROUP BY exchange ORDER BY n DESC;

SELECT CASE
         WHEN market_cap_rank IS NULL THEN 'null'
         WHEN market_cap_rank <= 100 THEN '1-100'
         WHEN market_cap_rank <= 200 THEN '101-200'
         WHEN market_cap_rank <= 400 THEN '201-400'
         WHEN market_cap_rank <= 600 THEN '401-600'
         WHEN market_cap_rank <= 1000 THEN '601-1000'
         ELSE '1001+'
       END AS bucket,
       COUNT(*) AS n
FROM assets WHERE in_universe
GROUP BY 1
ORDER BY MIN(COALESCE(market_cap_rank, 99999));

SELECT COUNT(*) AS not_in_universe_with_rank
FROM assets
WHERE NOT in_universe AND market_cap_rank IS NOT NULL;

SELECT COUNT(*) AS active_not_in_universe
FROM assets
WHERE is_active AND NOT in_universe;
SQL

echo
echo "=== RECENT UNIVERSE LOGS (worker+app, 7d) ==="
docker compose logs worker app --since 168h 2>&1 \
  | grep -E 'universe_refreshed|universe_target_not_reached|leverage_coverage_ready|leverage_venue_loaded|leverage_venue_failed|universe_symbol_without' \
  | tail -120 || true

echo
echo "=== SCHEDULED JOBS (universe) ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT * FROM scheduled_jobs WHERE job_name ILIKE '%universe%' OR job_name ILIKE '%refresh%' ORDER BY 1;" \
  2>/dev/null || docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c '\dt' | head -40
