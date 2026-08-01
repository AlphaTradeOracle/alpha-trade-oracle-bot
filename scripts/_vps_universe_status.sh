#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
grep -E '^(UNIVERSE_TARGET_COUNT|UNIVERSE_SIZE|UNIVERSE_SCAN_BATCH_SIZE|UNIVERSE_VERIFY_CANDLES)=' .env || true
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
          COUNT(*) FILTER (WHERE in_universe AND market_cap_rank <= 500) AS le500,
          COUNT(*) FILTER (WHERE in_universe AND market_cap_rank > 500) AS gt500,
          MAX(market_cap_rank) FILTER (WHERE in_universe) AS max_rank
   FROM assets;"
