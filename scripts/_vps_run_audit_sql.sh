#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
export PGPASSWORD
docker exec -e PGPASSWORD="$PGPASSWORD" -i alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle < /tmp/_vps_audit_paper.sql
