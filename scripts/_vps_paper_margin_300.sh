#!/usr/bin/env bash
# Switch paper stake to $300 / trade (×10 → $3000 notional) and rescale the ledger.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper margin \$300 start ====="

git fetch origin main
git reset --hard origin/main
echo "bot=$(git rev-parse --short HEAD)"

python3 - <<'PY'
from pathlib import Path
path = Path(".env")
text = path.read_text(encoding="utf-8")
repl = {
    "PAPER_MARGIN_PER_TRADE": "300",
    "PAPER_RISK_PER_TRADE_USD": "0",
    "PAPER_MAX_NOTIONAL_USD": "3000",
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

docker compose build app worker
docker compose up -d --no-deps app worker

echo "==> settings check"
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("margin", s.paper_margin_per_trade, "max_n", s.paper_max_notional_usd, "lev", s.paper_leverage)
assert s.paper_margin_per_trade == 300.0
assert s.paper_max_notional_usd == 3000.0
assert s.paper_risk_per_trade_usd == 0.0
PY

echo "==> BEFORE"
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT ROUND(margin_per_trade::numeric,2) mpt,
       ROUND(cash_balance::numeric,2) cash,
       ROUND(realized_pnl::numeric,2) realized
FROM paper_accounts WHERE name='default';
SELECT status, COUNT(*) n,
       ROUND(AVG(notional)::numeric,2) avg_n,
       ROUND(AVG(risk_amount)::numeric,2) avg_risk,
       ROUND(SUM(realized_pnl)::numeric,2) sum_pnl
FROM paper_positions GROUP BY status ORDER BY 1;
SQL

echo "==> rescale ledger 250 -> 300 (factor 1.2)"
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;

-- Only scale books that still look like the $250 stake regime.
DO $$
DECLARE
  factor numeric := 300.0 / 250.0;
  old_mpt numeric;
BEGIN
  SELECT margin_per_trade INTO old_mpt FROM paper_accounts WHERE name = 'default' FOR UPDATE;
  IF old_mpt IS NULL THEN
    RAISE EXCEPTION 'paper account missing';
  END IF;
  IF ABS(old_mpt - 300) < 0.01 THEN
    RAISE NOTICE 'already at $300 — skip rescale';
    RETURN;
  END IF;
  IF ABS(old_mpt - 250) > 0.01 THEN
    RAISE EXCEPTION 'unexpected margin_per_trade=% (expected 250)', old_mpt;
  END IF;

  UPDATE paper_fills f
  SET quantity = quantity * factor,
      fee = fee * factor,
      pnl = pnl * factor
  FROM paper_positions p
  WHERE f.position_id = p.id
    AND p.account_id = (SELECT id FROM paper_accounts WHERE name = 'default');

  UPDATE paper_positions
  SET initial_quantity = initial_quantity * factor,
      remaining_quantity = remaining_quantity * factor,
      margin_used = margin_used * factor,
      notional = notional * factor,
      realized_pnl = realized_pnl * factor,
      fees = fees * factor,
      risk_amount = risk_amount * factor,
      updated_at = NOW()
  WHERE account_id = (SELECT id FROM paper_accounts WHERE name = 'default');

  UPDATE paper_accounts a
  SET margin_per_trade = 300,
      realized_pnl = COALESCE((
        SELECT SUM(p.realized_pnl) FROM paper_positions p WHERE p.account_id = a.id
      ), 0),
      cash_balance = a.initial_balance
        + COALESCE((
            SELECT SUM(p.realized_pnl) FROM paper_positions p WHERE p.account_id = a.id
          ), 0)
        - COALESCE((
            SELECT SUM(p.margin_used)
            FROM paper_positions p
            WHERE p.account_id = a.id AND p.status IN ('open', 'pending')
          ), 0),
      updated_at = NOW()
  WHERE name = 'default';
END $$;

COMMIT;

-- Invariant: cash + open_margin = initial + realized
SELECT
  ROUND(cash_balance::numeric, 2) AS cash,
  ROUND(realized_pnl::numeric, 2) AS realized,
  ROUND(margin_per_trade::numeric, 2) AS mpt,
  ROUND((
    cash_balance
    + COALESCE((SELECT SUM(margin_used) FROM paper_positions
                WHERE account_id = paper_accounts.id AND status IN ('open','pending')), 0)
  )::numeric, 2) AS equity_cash_plus_margin,
  ROUND((initial_balance + realized_pnl)::numeric, 2) AS initial_plus_realized
FROM paper_accounts WHERE name = 'default';
SQL

echo "==> AFTER"
docker compose exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
SELECT status, COUNT(*) n,
       ROUND(AVG(NULLIF(margin_used,0))::numeric,2) avg_m_openish,
       ROUND(AVG(notional)::numeric,2) avg_n,
       ROUND(AVG(risk_amount)::numeric,2) avg_risk,
       ROUND(SUM(realized_pnl)::numeric,2) sum_pnl
FROM paper_positions GROUP BY status ORDER BY 1;
SELECT symbol, status,
       ROUND(margin_used::numeric,2) m,
       ROUND(notional::numeric,2) n,
       ROUND(risk_amount::numeric,2) risk,
       ROUND(realized_pnl::numeric,2) pnl
FROM paper_positions
WHERE status IN ('open','pending')
ORDER BY status, opened_at;
SQL

sleep 3
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_m300.json
python3 - <<'PY'
import json
d=json.load(open("/tmp/desk_m300.json"))
p=d["portfolio"]
print(
    "DESK equity=", p.get("equity"),
    "cash=", p.get("cash"),
    "realized=", p.get("realizedPnl"),
    "marginLocked=", p.get("marginLocked"),
    "open=", p.get("openPositions"),
    "closed=", p.get("closedTrades"),
)
opens=d.get("openTrades") or []
for t in opens[:5]:
    print("OPEN", t.get("symbol"), "margin", t.get("margin"), "notional", t.get("notional"), "r", t.get("r"))
closed=d.get("closedTrades") or []
for t in closed[:3]:
    print("CLOSED", t.get("symbol"), "margin", t.get("margin"), "realized", t.get("realized"), "r", t.get("r"))
assert abs(float(p.get("marginLocked") or 0) - 300.0) < 1.0 or float(p.get("openPositions") or 0) == 0
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper margin \$300 done ====="
