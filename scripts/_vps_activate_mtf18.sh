#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose cp app/strategies/weights.py worker:/app/app/strategies/weights.py
docker compose cp scripts/activate_strategy_weights.py worker:/tmp/activate_strategy_weights.py
docker compose exec -T worker python /tmp/activate_strategy_weights.py
# recreate so long-running processes pick up module + DB active version on next resolve
docker compose up -d --force-recreate --no-deps worker app
sleep 6
docker compose exec -T worker python - <<'PY'
from app.strategies.weights import DEFAULT_WEIGHTS
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import StrategyWeights
import asyncio

async def main():
    print('DEFAULT', DEFAULT_WEIGHTS.model_dump())
    async with session_scope() as session:
        active = await StrategyRepository(session).get_active_version(DEFAULT_STRATEGY_NAME)
        cur = StrategyWeights.from_db_columns(active)
        print('ACTIVE_v', active.version, cur.model_dump())
asyncio.run(main())
PY
