#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT a.symbol, pp.exit_reason, pp.timeframe,
       pp.opened_at AT TIME ZONE 'UTC' AS opened_utc,
       pp.closed_at AT TIME ZONE 'UTC' AS closed_utc,
       to_char(pp.closed_at AT TIME ZONE 'Europe/Berlin', 'DD.MM.YYYY HH24:MI:SS') AS closed_berlin
FROM paper_positions pp
JOIN assets a ON a.id = pp.asset_id
WHERE pp.status = 'closed'
ORDER BY pp.closed_at DESC NULLS LAST
LIMIT 15;

SELECT exit_reason, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE EXTRACT(MINUTE FROM closed_at AT TIME ZONE 'UTC') = 0
                          AND EXTRACT(SECOND FROM closed_at AT TIME ZONE 'UTC') < 1) AS on_hour_utc
FROM paper_positions
WHERE status = 'closed' AND closed_at IS NOT NULL
GROUP BY 1
ORDER BY n DESC;
SQL
