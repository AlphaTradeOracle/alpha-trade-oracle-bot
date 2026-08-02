#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -i -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle < /tmp/kpi.sql
echo "--- log ---"
grep -E 'AFTER|WR\||ACCT|TOP|BOT|OPEN|equity|closed:|opened:|finish paper' /tmp/finish_paper_rebuild.log | tail -n 50
