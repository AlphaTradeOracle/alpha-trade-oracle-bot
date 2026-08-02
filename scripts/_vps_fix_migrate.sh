#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
echo "==> alembic heads in repo"
ls -la alembic/versions/0006_market_context.py
echo "==> current revision (worker image)"
docker compose exec -T worker alembic current || true
docker compose exec -T worker alembic heads || true
echo "==> rebuild migrate + upgrade"
docker compose build migrate worker
docker compose run --rm migrate
docker compose exec -T worker alembic current
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT table_name, column_name FROM information_schema.columns WHERE column_name='market_context' ORDER BY 1;"
