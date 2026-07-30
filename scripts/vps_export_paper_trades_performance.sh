#!/usr/bin/env bash
# Export full paper trades snapshot for performance canvas (pipe-delimited).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
cat scripts/export_paper_trades_performance.sql | docker exec -i -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -f -
