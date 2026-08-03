#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
SINCE='2026-07-31T16:32:35+00:00'

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<SQL
-- signals since reset
SELECT count(*) AS signals_since_reset
FROM signals
WHERE created_at >= '$SINCE';

SELECT direction, count(*) AS n
FROM signals
WHERE created_at >= '$SINCE'
GROUP BY 1 ORDER BY 2 DESC;

SELECT
  count(*) FILTER (WHERE score <= 30) AS shortish_score_le_30,
  count(*) FILTER (WHERE score >= 75) AS longish_score_ge_75,
  count(*) AS total
FROM signals
WHERE created_at >= '$SINCE';

SELECT date_trunc('day', created_at) AS day, count(*) AS signals
FROM signals
WHERE created_at >= '$SINCE'
GROUP BY 1 ORDER BY 1;

-- paper outcomes
SELECT status, count(*) FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
GROUP BY 1 ORDER BY 1;

SELECT
  count(*) FILTER (WHERE status='closed') AS closed,
  count(*) FILTER (WHERE status='cancelled') AS cancelled,
  count(DISTINCT symbol) FILTER (WHERE status='closed') AS closed_symbols,
  count(DISTINCT symbol) FILTER (WHERE status IN ('closed','cancelled')) AS all_symbols_touched
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default');
SQL
