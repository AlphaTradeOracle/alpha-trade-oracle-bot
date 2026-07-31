#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
echo "== env =="
grep -E '^(TELEGRAM_SIGNAL_DISPATCH|PAPER_MAX_|PAPER_INITIAL)' .env || true
echo "== account =="
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT initial_balance, cash_balance, realized_pnl FROM paper_accounts WHERE name='default';"
echo "== positions =="
docker exec -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT status, COUNT(*) FROM paper_positions GROUP BY status ORDER BY status;"
