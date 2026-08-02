#!/usr/bin/env bash
# Switch paper sizing to $200 margin / $100 risk, rebuild ledger, restart API.
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

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper \$200 sizing start ====="

echo "==> BASELINE"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'BASE|' || status || '|' || COUNT(*) || '|avg_m=' || ROUND(AVG(margin)::numeric,2)
  || '|avg_n=' || ROUND(AVG(notional)::numeric,2)
  || '|pnl=' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'ACCT|' || ROUND(margin_per_trade::numeric,2) || '|' || ROUND(cash_balance::numeric,2)
  || '|' || ROUND(realized_pnl::numeric,2)
FROM paper_accounts WHERE name='default';
SQL

echo "==> Update .env sizing (2x from \$100 / \$50 / \$1500)"
python3 - <<'PY'
from pathlib import Path
path = Path(".env")
text = path.read_text(encoding="utf-8")
repl = {
    "PAPER_MARGIN_PER_TRADE": "200",
    "PAPER_RISK_PER_TRADE_USD": "100",
    "PAPER_MAX_NOTIONAL_USD": "3000",
}
lines = []
seen = set()
for line in text.splitlines():
    key = line.split("=", 1)[0] if "=" in line and not line.strip().startswith("#") else None
    if key in repl:
        lines.append(f"{key}={repl[key]}")
        seen.add(key)
    else:
        lines.append(line)
for key, val in repl.items():
    if key not in seen:
        lines.append(f"{key}={val}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("updated", ", ".join(f"{k}={v}" for k, v in repl.items()))
PY
grep -E '^PAPER_MARGIN_PER_TRADE=|^PAPER_RISK_PER_TRADE_USD=|^PAPER_MAX_NOTIONAL_USD=|^PAPER_LEVERAGE=' .env

echo "==> Sync code + rebuild worker/app"
git fetch origin main
git reset --hard origin/main
# re-apply env edits after reset? .env is usually gitignored — keep as-is
grep -E '^PAPER_MARGIN_PER_TRADE=|^PAPER_RISK_PER_TRADE_USD=|^PAPER_MAX_NOTIONAL_USD=' .env
docker compose build worker app
docker compose up -d --no-deps worker app

echo "==> Paper rebuild (reset-era symbols)"
docker compose run --rm --no-deps worker \
  python -m app.cli paper rebuild \
  --since "${SINCE}" \
  --all-signals \
  --symbols-file "${SYMBOLS_FILE}"

echo "==> AFTER"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'AFTER|' || status || '|' || COUNT(*) || '|avg_m=' || ROUND(AVG(margin)::numeric,2)
  || '|avg_n=' || ROUND(AVG(notional)::numeric,2)
  || '|pnl=' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'WR|' || wins || '|' || losses || '|' || ROUND(wr*100,1)
FROM (
  SELECT
    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
    CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl > 0)::float/COUNT(*) ELSE 0 END AS wr
  FROM paper_positions WHERE status='closed'
) s;
SELECT 'ACCT|' || ROUND(margin_per_trade::numeric,2) || '|' || ROUND(cash_balance::numeric,2)
  || '|' || ROUND(realized_pnl::numeric,2)
FROM paper_accounts WHERE name='default';
SELECT 'OPEN|' || symbol || '|m=' || ROUND(margin::numeric,2) || '|n=' || ROUND(notional::numeric,2)
  || '|r=' || ROUND(COALESCE(risk_amount,0)::numeric,2)
FROM paper_positions WHERE status='open' ORDER BY opened_at;
SQL

# warm desk cache
sleep 2
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk.json
python3 - <<'PY'
import json
p=json.load(open("/tmp/desk.json"))["portfolio"]
print(
    "DESK equity=", p.get("equity"),
    "realized=", p.get("realizedPnl"),
    "cash=", p.get("cash"),
    "marginLocked=", p.get("marginLocked"),
    "closed=", p.get("closedTrades"),
    "open=", p.get("openPositions"),
    "winRatePct=", p.get("winRatePct"),
)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper \$200 sizing done ====="
