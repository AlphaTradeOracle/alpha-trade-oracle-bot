#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\echo === CLOSED BY DAY (current book, since Aug1 14:00 rebuild) ===
SELECT date_trunc('day', opened_at) AS day, count(*) AS closed
FROM paper_positions
WHERE account_id=(SELECT id FROM paper_accounts WHERE name='default')
  AND status='closed'
GROUP BY 1 ORDER BY 1;

\echo === ACTIONABLE SIGNAL COUNTS ===
SELECT
  count(*) FILTER (WHERE created_at >= '2026-07-31T16:32:35+00:00'
    AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')) AS actionable_since_jul31,
  count(*) FILTER (WHERE created_at >= '2026-08-01T14:00:00+00:00'
    AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')) AS actionable_since_aug1_14,
  count(*) FILTER (
    WHERE created_at >= '2026-07-31T16:32:35+00:00'
      AND created_at < '2026-08-01T14:00:00+00:00'
      AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT')
  ) AS actionable_jul31_to_aug1_14,
  count(*) FILTER (
    WHERE created_at >= '2026-07-31T16:32:35+00:00'
      AND direction IN ('SHORT','STRONG_SHORT')
      AND score > 18 AND score <= 30
  ) AS short_gate_jul31,
  count(*) FILTER (
    WHERE created_at >= '2026-08-01T14:00:00+00:00'
      AND direction IN ('SHORT','STRONG_SHORT')
      AND score > 18 AND score <= 30
  ) AS short_gate_aug1_14
FROM signals;
SQL

# show rebuild stream limit in code
docker compose exec -T worker python - <<'PY'
import inspect
from app.services import paper_trading_service as pts
src = inspect.getsource(pts.PaperTradingService._rebuild_from_signal_stream)
for line in src.splitlines():
    if "limit" in line or "list_since" in line:
        print(line)
PY
