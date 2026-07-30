#!/usr/bin/env bash
# Export live paper KPI snapshot from VPS Postgres (pipe-delimited).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
cat scripts/export_paper_live_snapshot.sql | docker exec -i -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -f -
