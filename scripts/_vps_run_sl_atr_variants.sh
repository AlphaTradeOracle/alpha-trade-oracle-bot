#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/alpha-trade-oracle-bot}"
OUT="${OUT:-/tmp/sl_atr_variants.json}"
LOG="${LOG:-/tmp/sl_atr_variants.log}"
SERVICE="${SERVICE:-worker}"

cd "$APP_DIR"
CID=$(docker compose ps -q "$SERVICE")
docker cp "$APP_DIR/scripts/simulate_sl_atr_variants.py" "$CID:/app/scripts/simulate_sl_atr_variants.py"

docker compose exec -d "$SERVICE" bash -c \
  "python -u /app/scripts/simulate_sl_atr_variants.py --out ${OUT} > ${LOG} 2>&1"

sleep 4
echo "OUT=${OUT} LOG=${LOG}"
docker compose exec -T "$SERVICE" bash -c "tail -n 40 ${LOG} || true"
