#!/usr/bin/env bash
set -euo pipefail
PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' /opt/alpha-trade-oracle-bot/.env | cut -d= -f2-)
export PGPASSWORD
docker exec -e PGPASSWORD -i alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle < /tmp/_after_tl_rebuild.sql
