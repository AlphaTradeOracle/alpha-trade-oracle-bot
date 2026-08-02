#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
export PGPASSWORD="$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
docker exec -i -e PGPASSWORD="$PGPASSWORD" alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle \
  < scripts/_vps_paper_since_reset.sql \
  > /tmp/paper_since_reset.txt
wc -l /tmp/paper_since_reset.txt
head -n 5 /tmp/paper_since_reset.txt
