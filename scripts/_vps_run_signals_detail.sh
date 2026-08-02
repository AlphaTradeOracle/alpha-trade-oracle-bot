#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
set -a
# shellcheck disable=SC1091
source .env
set +a
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-alpha_trade_oracle}" -d "${POSTGRES_DB:-alpha_trade_oracle}" \
  < /tmp/_vps_signals_detail.sql
