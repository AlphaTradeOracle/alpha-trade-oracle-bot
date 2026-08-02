#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
grep -E '^(UNIVERSE_TARGET_COUNT|UNIVERSE_SIZE|UNIVERSE_SCAN_BATCH_SIZE)=' .env || true
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT COUNT(*) FILTER (WHERE in_universe) AS in_universe,
          COUNT(*) FILTER (WHERE in_universe AND is_active) AS active_universe,
          COUNT(*) FILTER (WHERE is_active) AS active_total
   FROM assets;"
