#!/usr/bin/env bash
# Fixed $200 margin per paper trade (×10 → $2000 notional), rebuild ledger.
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

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper fixed \$200 margin start ====="

python3 - <<'PY'
from pathlib import Path
path = Path(".env")
text = path.read_text(encoding="utf-8")
repl = {
    "PAPER_MARGIN_PER_TRADE": "200",
    "PAPER_RISK_PER_TRADE_USD": "0",
    "PAPER_MAX_NOTIONAL_USD": "2000",
    "PAPER_LEVERAGE": "10",
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
print("env", ", ".join(f"{k}={v}" for k, v in repl.items()))
PY
grep -E '^PAPER_MARGIN_PER_TRADE=|^PAPER_RISK_PER_TRADE_USD=|^PAPER_MAX_NOTIONAL_USD=|^PAPER_LEVERAGE=' .env

git fetch origin main
git reset --hard origin/main
# .env is gitignored — re-assert after reset
python3 - <<'PY'
from pathlib import Path
path = Path(".env")
text = path.read_text(encoding="utf-8")
repl = {
    "PAPER_MARGIN_PER_TRADE": "200",
    "PAPER_RISK_PER_TRADE_USD": "0",
    "PAPER_MAX_NOTIONAL_USD": "2000",
    "PAPER_LEVERAGE": "10",
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
PY

docker compose build worker app
docker compose up -d --no-deps worker app

echo "==> verify container env"
docker compose run --rm --no-deps worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print(
    "settings",
    "margin", s.paper_margin_per_trade,
    "risk", s.paper_risk_per_trade_usd,
    "max_n", s.paper_max_notional_usd,
    "lev", s.paper_leverage,
)
PY

echo "==> Paper rebuild"
docker compose run --rm --no-deps worker \
  python -m app.cli paper rebuild \
  --since "${SINCE}" \
  --all-signals \
  --symbols-file "${SYMBOLS_FILE}"

echo "==> AFTER"
run_sql <<'SQL'
\pset tuples_only on
\pset format unaligned
SELECT 'AFTER|' || status || '|' || COUNT(*)
  || '|avg_m=' || ROUND(AVG(margin)::numeric,2)
  || '|avg_n=' || ROUND(AVG(notional)::numeric,2)
  || '|pnl=' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions GROUP BY status ORDER BY status;
SELECT 'ACCT|margin_setting=' || ROUND(margin_per_trade::numeric,2)
  || '|cash=' || ROUND(cash_balance::numeric,2)
  || '|realized=' || ROUND(realized_pnl::numeric,2)
FROM paper_accounts WHERE name='default';
SELECT 'OPEN|' || symbol || '|m=' || ROUND(margin::numeric,2)
  || '|n=' || ROUND(notional::numeric,2)
FROM paper_positions WHERE status='open' ORDER BY opened_at;
SQL

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

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper fixed \$200 margin done ====="
