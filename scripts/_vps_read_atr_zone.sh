#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
grep -E '^(ATR_MULTIPLIER|PAPER_RETEST_ZONE|PAPER_INITIAL|PAPER_MARGIN|PAPER_MAX_OPEN|SIGNAL_SHORT|SIGNAL_MIN_SCORE|PAPER_RETEST_ENTRY)=' .env | sort
echo ---
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print({
  "atr": s.atr_multiplier,
  "near": s.paper_retest_zone_near,
  "far": s.paper_retest_zone_far,
  "short_max": s.signal_short_max_score,
  "short_min": s.signal_short_min_score,
  "long_min": s.signal_min_score,
  "retest": s.paper_retest_entry_enabled,
  "max_open": s.paper_max_open_positions,
})
PY
