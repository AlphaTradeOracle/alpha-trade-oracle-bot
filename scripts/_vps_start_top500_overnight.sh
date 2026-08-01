#!/usr/bin/env bash
# Overnight: universe → 500 pairs, backfill candles, 7d 300-vs-500 compare.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
LOG=/opt/alpha-trade-oracle-bot/exports/top500_overnight.log
mkdir -p exports
exec >>"$LOG" 2>&1

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) top500 overnight start ====="

if grep -q '^UNIVERSE_TARGET_COUNT=' .env; then
  sed -i 's/^UNIVERSE_TARGET_COUNT=.*/UNIVERSE_TARGET_COUNT=500/' .env
else
  echo 'UNIVERSE_TARGET_COUNT=500' >> .env
fi
if grep -q '^UNIVERSE_SCAN_BATCH_SIZE=' .env; then
  sed -i 's/^UNIVERSE_SCAN_BATCH_SIZE=.*/UNIVERSE_SCAN_BATCH_SIZE=500/' .env
else
  echo 'UNIVERSE_SCAN_BATCH_SIZE=500' >> .env
fi
if grep -q '^UNIVERSE_SIZE=' .env; then
  sed -i 's/^UNIVERSE_SIZE=.*/UNIVERSE_SIZE=1000/' .env
else
  echo 'UNIVERSE_SIZE=1000' >> .env
fi

grep -E '^(UNIVERSE_TARGET_COUNT|UNIVERSE_SIZE|UNIVERSE_SCAN_BATCH_SIZE|UNIVERSE_VERIFY_CANDLES)=' .env

ids=$(docker ps -aq --filter name=alpha-trade-oracle-worker || true)
if [[ -n "${ids}" ]]; then
  docker rm -f ${ids} || true
fi
docker compose up -d worker
sleep 8
docker compose ps worker

# Sync compare scripts (in case image is stale)
docker compose cp scripts/compare_universe_topn.py worker:/app/scripts/compare_universe_topn.py || true
docker compose cp scripts/optimize_strategy_top300.py worker:/app/scripts/optimize_strategy_top300.py || true

echo "----- universe refresh (target 500) -----"
docker compose exec -T worker python -m app.cli universe refresh

echo "----- post-refresh counts -----"
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
          COUNT(*) FILTER (WHERE in_universe AND market_cap_rank <= 500) AS le500,
          COUNT(*) FILTER (WHERE in_universe AND market_cap_rank > 500) AS gt500,
          MAX(market_cap_rank) FILTER (WHERE in_universe) AS max_rank
   FROM assets;"

echo "----- candle backfill 60d (for 7d compare indicators) -----"
docker compose exec -T worker python -m app.cli data backfill --days 60 --limit 500

echo "----- 7d universe compare -----"
OUT=/tmp/universe_300_vs_500_7d.json
docker compose exec -T -e PYTHONUNBUFFERED=1 worker \
  python /app/scripts/compare_universe_topn.py \
    --top 500 --days 7 --timeframe 1h --workers 2 \
    --out "$OUT" \
  || echo "WARN: compare exited non-zero"

# Copy result into exports if present
docker compose cp worker:"$OUT" exports/universe_300_vs_500_7d.json 2>/dev/null \
  || cp -f "$OUT" exports/universe_300_vs_500_7d.json 2>/dev/null \
  || true

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) top500 overnight done ====="
ls -la exports/universe_300_vs_500_7d.json exports/top500_overnight.log 2>/dev/null || true
