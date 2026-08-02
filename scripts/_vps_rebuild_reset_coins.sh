#!/usr/bin/env bash
# Rebuild paper ledger with soft-blend gates, only original reset-era coins.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
SYMBOLS_FILE="${SYMBOLS_FILE:-scripts/paper_reset_symbols.txt}"

export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD

run_sql() {
  docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
    psql -U alpha_trade_oracle -d alpha_trade_oracle -t -A -f -
}

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) reset-coins paper rebuild start ====="
echo "since=${SINCE}"
echo "symbols_file=${SYMBOLS_FILE}"
wc -l "$SYMBOLS_FILE"
grep -vE '^\s*(#|$)' "$SYMBOLS_FILE" | tr '\n' ' '; echo

echo "==> BASELINE KPIs"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'BASELINE|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SQL

echo "==> rebuild worker image (patched CLI/service)"
docker compose build worker
docker compose up -d --no-deps worker

echo "==> Paper rebuild (allowlist only; soft-blend scores already in DB)"
docker compose run --rm --no-deps worker \
  python -m app.cli paper rebuild \
  --since "${SINCE}" \
  --all-signals \
  --symbols-file "${SYMBOLS_FILE}"

echo "==> AFTER KPIs"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1) || '|' || pf
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr,
    CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)) > 0
      THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0),0) / ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl <= 0),0)))::numeric,2)
      ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'ACCT|' || COALESCE(ROUND(realized_pnl::numeric,2),0) || '|' || COALESCE(ROUND(cash_balance::numeric,2),0)
FROM paper_accounts WHERE name='default';
SELECT 'SYM|' || symbol || '|' || status || '|' || ROUND(COALESCE(realized_pnl,0)::numeric,2) || '|' || COALESCE(exit_reason,'')
FROM paper_positions ORDER BY opened_at;
SQL

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk.json"))
p=d.get("portfolio") or {}
print(
    "DESK",
    "equity", p.get("equity"),
    "realized", p.get("realizedPnl"),
    "closed", p.get("closedTrades"),
    "open", p.get("openPositions"),
    "pending", p.get("pendingOrders"),
)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) reset-coins paper rebuild done ====="
