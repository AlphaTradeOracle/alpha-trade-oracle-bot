#!/usr/bin/env bash
# Voller Daten-Load: Universe-Refresh + Scan aller handelbaren Top-MCap-Coins.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/alpha-trade-oracle-bot}"
LOG="${LOG:-/tmp/full-data-load.log}"
BATCH_SIZE="${UNIVERSE_SCAN_BATCH_SIZE:-1000}"

cd "$APP_DIR"

exec > >(tee -a "$LOG") 2>&1

echo "=== START $(date -Is) ==="

echo "=== Universe Refresh ==="
docker compose exec -T worker python -m app.cli universe refresh

echo "=== Universe + DB Baseline ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS universe FROM assets WHERE in_universe=true;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS scanned FROM assets WHERE in_universe=true AND last_scanned_at IS NOT NULL;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS candles FROM market_candles;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS snapshots FROM indicator_snapshots;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS signals FROM signals;"

echo "=== Full Universe Scan (batch=$BATCH_SIZE) ==="
docker compose exec -T -e "UNIVERSE_SCAN_BATCH_SIZE=$BATCH_SIZE" worker \
  python -m app.cli scan --universe --no-dispatch

echo "=== ERGEBNIS $(date -Is) ==="
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS universe FROM assets WHERE in_universe=true;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS scanned FROM assets WHERE in_universe=true AND last_scanned_at IS NOT NULL;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS candles FROM market_candles;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS snapshots FROM indicator_snapshots;"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) AS signals FROM signals;"

echo "=== DONE $(date -Is) ==="
