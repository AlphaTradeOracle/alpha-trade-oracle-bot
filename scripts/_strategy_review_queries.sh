#!/usr/bin/env bash
# Read-only strategy review queries (pipe-delimited). Mirrors vps_export_paper_trades_performance.sh.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
cat scripts/_strategy_review_queries.sql | docker exec -i -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -f -
