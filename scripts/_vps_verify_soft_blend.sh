#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
docker compose run --rm migrate
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT table_name, column_name FROM information_schema.columns WHERE column_name='market_context' ORDER BY 1;"
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
assert s.market_regime_enabled is True
assert s.institutional_enforce_gates is False
assert s.signal_short_max_score == 30.0
print("OK soft-blend live")
print("enforce_gates", s.institutional_enforce_gates)
print("regime", s.market_regime_enabled, "hard_veto", s.market_regime_hard_veto)
print("short_max", s.signal_short_max_score)
PY
