#!/bin/bash
set -eu
SP=/opt/venv/lib/python3.12/site-packages/app
cd /opt/alpha-trade-oracle-bot

# Sync repo files into running worker site-packages + /app sources
for pair in \
  "signals/risk.py:/tmp/risk.py" \
  "backtesting/engine.py:/tmp/engine.py" \
  "services/paper_trading_service.py:/tmp/paper_trading_service.py" \
  "services/analysis_service.py:/tmp/analysis_service.py" \
  "repositories/paper_repository.py:/tmp/paper_repository.py"
do
  dest="${pair%%:*}"
  src="${pair##*:}"
  docker cp "$src" "alpha-trade-oracle-worker:${SP}/${dest}"
  docker cp "$src" "alpha-trade-oracle-worker:/app/app/${dest}"
done
docker cp /tmp/cli.py alpha-trade-oracle-worker:/app/app/cli.py
docker cp /tmp/cli.py "alpha-trade-oracle-worker:${SP}/cli.py"

echo "Running paper rebuild since 2026-07-28 ..."
docker compose exec -T worker python -m app.cli paper rebuild --since 2026-07-28 --all-qualifying
