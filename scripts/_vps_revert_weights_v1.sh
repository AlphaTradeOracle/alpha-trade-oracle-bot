#!/usr/bin/env bash
# Revert coin category weights to v1 (Structure 16.38%, MTF 10.46%).
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/revert_weights_v1.log
: >"$LOG"

for f in app/strategies/weights.py scripts/activate_strategy_weights.py tests/test_weights.py; do
  sed -i 's/\r$//' "/tmp/$(basename "$f")" 2>/dev/null || true
done

cp /tmp/weights.py app/strategies/weights.py
cp /tmp/activate_strategy_weights.py scripts/activate_strategy_weights.py
cp /tmp/test_weights.py tests/test_weights.py 2>/dev/null || true

docker compose cp app/strategies/weights.py worker:/app/app/strategies/weights.py
docker compose cp app/strategies/weights.py app:/app/app/strategies/weights.py
docker compose cp scripts/activate_strategy_weights.py worker:/app/scripts/activate_strategy_weights.py

echo "=== activate DEFAULT_WEIGHTS (v1) ===" | tee -a "$LOG"
docker compose exec -T worker python /app/scripts/activate_strategy_weights.py 2>&1 | tee -a "$LOG"

echo "=== verify ===" | tee -a "$LOG"
docker compose exec -T worker python - <<'PY' | tee -a "$LOG"
import asyncio
from app.strategies.weights import DEFAULT_WEIGHTS
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import StrategyWeights
from app.core.logging import configure_logging
configure_logging("ERROR", json_output=False)
print("DEFAULT", DEFAULT_WEIGHTS.model_dump())
async def main():
    async with session_scope() as s:
        a = await StrategyRepository(s).get_active_version(DEFAULT_STRATEGY_NAME)
        w = StrategyWeights.from_db_columns(a)
        print("ACTIVE_v", a.version, w.model_dump())
        print("match", w.model_dump() == DEFAULT_WEIGHTS.model_dump())
asyncio.run(main())
PY
echo "===== done $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" | tee -a "$LOG"
