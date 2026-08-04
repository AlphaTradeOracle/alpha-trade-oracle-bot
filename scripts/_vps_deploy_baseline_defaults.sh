#!/usr/bin/env bash
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/baseline_rollback
cd "$APP"

cp "$SRC/config.py" "$APP/app/core/config.py"
cp "$SRC/retest_entry.py" "$APP/app/signals/retest_entry.py"
cp "$SRC/risk.py" "$APP/app/signals/risk.py"
cp "$SRC/strategy.py" "$APP/app/models/strategy.py"
cp "$SRC/strategy_repository.py" "$APP/app/repositories/strategy_repository.py"
cp "$SRC/engine.py" "$APP/app/backtesting/engine.py"

# Keep env on baseline
if grep -q '^ATR_MULTIPLIER=' .env; then sed -i 's/^ATR_MULTIPLIER=.*/ATR_MULTIPLIER=1.5/' .env
else echo 'ATR_MULTIPLIER=1.5' >> .env; fi
if grep -q '^PAPER_RETEST_ZONE_NEAR=' .env; then sed -i 's/^PAPER_RETEST_ZONE_NEAR=.*/PAPER_RETEST_ZONE_NEAR=0.55/' .env
else echo 'PAPER_RETEST_ZONE_NEAR=0.55' >> .env; fi
if grep -q '^PAPER_RETEST_ZONE_FAR=' .env; then sed -i 's/^PAPER_RETEST_ZONE_FAR=.*/PAPER_RETEST_ZONE_FAR=1.0/' .env
else echo 'PAPER_RETEST_ZONE_FAR=1.0' >> .env; fi

for c in alpha-trade-oracle-worker alpha-trade-oracle-app; do
  docker cp "$APP/app/core/config.py" "$c:/app/app/core/config.py"
  docker cp "$APP/app/signals/retest_entry.py" "$c:/app/app/signals/retest_entry.py"
  docker cp "$APP/app/signals/risk.py" "$c:/app/app/signals/risk.py"
  docker cp "$APP/app/models/strategy.py" "$c:/app/app/models/strategy.py"
  docker cp "$APP/app/repositories/strategy_repository.py" "$c:/app/app/repositories/strategy_repository.py"
  docker cp "$APP/app/backtesting/engine.py" "$c:/app/app/backtesting/engine.py"
done

docker compose -f docker-compose.yml --env-file .env restart worker app
sleep 8
docker compose -f docker-compose.yml --env-file .env exec -T postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "UPDATE strategy_versions SET atr_multiplier = 1.5 WHERE atr_multiplier IS DISTINCT FROM 1.5;" >/dev/null || true

docker compose -f docker-compose.yml --env-file .env exec -T worker python - <<'PY'
from app.core.config import get_settings
from app.signals.retest_entry import ZONE_NEAR, ZONE_FAR
from app.signals.risk import RiskConfig
s = get_settings()
print({
    "atr": s.atr_multiplier,
    "zone": (s.paper_retest_zone_near, s.paper_retest_zone_far),
    "retest_defaults": (float(ZONE_NEAR), float(ZONE_FAR)),
    "risk_default_atr": RiskConfig().atr_multiplier,
})
PY

curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_ab.json
python3 /tmp/_dump_desk_equity.py
echo "===== baseline defaults deployed ====="
