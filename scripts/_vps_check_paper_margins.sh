#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "\d paper_positions" | head -60
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, symbol, ROUND(margin_used::numeric,2) m, ROUND(notional::numeric,2) n, ROUND(COALESCE(realized_pnl,0)::numeric,2) pnl FROM paper_positions WHERE status IN ('open','pending') ORDER BY status, opened_at;" 2>/dev/null || \
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, symbol, ROUND(initial_margin::numeric,2) m, ROUND(notional::numeric,2) n FROM paper_positions WHERE status IN ('open','pending') ORDER BY status, opened_at;" 2>/dev/null || true
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT ROUND(margin_per_trade::numeric,2) margin_setting, ROUND(cash_balance::numeric,2) cash, ROUND(realized_pnl::numeric,2) realized FROM paper_accounts WHERE name='default';"
grep -E '^PAPER_MARGIN_PER_TRADE=|^PAPER_RISK_PER_TRADE_USD=|^PAPER_MAX_NOTIONAL' .env
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c 'import sys,json;d=json.load(sys.stdin);p=d["portfolio"];print({k:p.get(k) for k in ("equity","realizedPnl","cash","marginLocked","openPositions","closedTrades","winRatePct")});
ops=d.get("openTrades") or d.get("open") or [];
print("open_sample", [(t.get("symbol"), t.get("margin"), t.get("notional") or t.get("positionSize")) for t in (ops[:5] if isinstance(ops,list) else [])])'
