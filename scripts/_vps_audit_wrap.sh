#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== LATEST SCAN + JOB ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT job_key, last_status, last_run_at, last_success_at, next_run_at,
       ROUND(EXTRACT(EPOCH FROM (last_success_at - last_run_at))/60.0,1) AS duration_min
FROM scheduled_jobs WHERE job_key='market_scan:15m';

SELECT created_at, payload
FROM application_events
WHERE event_type='market_scan_completed'
ORDER BY created_at DESC LIMIT 3;
SQL

echo
echo "=== PAPER POSITIONS ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
SELECT status, COUNT(*) FROM paper_positions GROUP BY 1 ORDER BY 1;
SELECT pp.opened_at, a.symbol, pp.side, pp.status,
       ROUND(pp.entry_price::numeric,6) AS entry,
       ROUND(COALESCE(pp.unrealized_pnl,0)::numeric,2) AS upnl
FROM paper_positions pp
JOIN assets a ON a.id = pp.asset_id
WHERE pp.status IN ('open','pending')
ORDER BY pp.opened_at DESC;
SQL

echo
echo "=== CONFIDENCE / ADX ENV ==="
grep -E '^(MIN_CONFIDENCE|SIGNAL_MIN|INSTITUTIONAL|CONFIDENCE|SOFT_BLEND|SCORE_BLEND)' .env | sort || true
grep -iE 'confidence|soft_blend|min_trade' .env | head -40 || true

echo
echo "=== TOP COINS FINAL ==="
curl -fsS http://127.0.0.1:8000/api/v1/desk/top-coins?limit=10 | python3 -c 'import sys,json; c=json.load(sys.stdin)["coins"]; print(" ".join(x["symbol"] for x in c)); print("ok",len(c)==10)'
curl -fsS -o /dev/null -w "site=%{http_code}\n" https://alpha-trade-oracle.com/
echo DONE
