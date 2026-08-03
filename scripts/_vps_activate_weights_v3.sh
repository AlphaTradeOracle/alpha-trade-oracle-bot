#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose up -d --build --no-deps worker app
# wait for worker
for i in $(seq 1 40); do
  if docker compose exec -T worker python -c 'from app.strategies.weights import DEFAULT_WEIGHTS; assert abs(DEFAULT_WEIGHTS.multi_timeframe-0.18)<1e-9; assert abs(DEFAULT_WEIGHTS.market_structure-0.18)<1e-9; print(DEFAULT_WEIGHTS.model_dump())' 2>/dev/null; then
    break
  fi
  sleep 3
done
docker compose exec -T worker python - <<'PY'
import asyncio
from scripts.activate_strategy_weights import run_activate
# module path may not include scripts/; call logic inline
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import DEFAULT_WEIGHTS, StrategyWeights

NOTES = (
    "Paper forward (2026-08-03): MTF 18% + Structure 18%, "
    "remaining categories scaled proportionally from v2"
)

async def main():
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    target = DEFAULT_WEIGHTS
    async with session_scope() as session:
        repo = StrategyRepository(session)
        active = await repo.get_active_version(DEFAULT_STRATEGY_NAME)
        if active is not None:
            current = StrategyWeights.from_db_columns(active)
            if current.model_dump() == target.model_dump():
                print(f"Bereits aktiv: default v{active.version}")
                print(target.model_dump())
                return
        version = await repo.create_version(
            target,
            name=DEFAULT_STRATEGY_NAME,
            min_score=settings.signal_min_score,
            min_risk_reward_ratio=settings.min_risk_reward_ratio,
            atr_multiplier=settings.atr_multiplier,
            notes=NOTES,
            activate=True,
        )
        print(f"Aktiviert: default v{version.version}")
        print(target.model_dump())

asyncio.run(main())
PY
# bounce so services reload active version
docker compose up -d --force-recreate --no-deps worker app
sleep 6
docker compose exec -T worker python - <<'PY'
import asyncio
from app.strategies.weights import DEFAULT_WEIGHTS
from app.database.session import session_scope
from app.repositories.strategy_repository import DEFAULT_STRATEGY_NAME, StrategyRepository
from app.strategies.weights import StrategyWeights

async def main():
    print('DEFAULT', DEFAULT_WEIGHTS.model_dump())
    async with session_scope() as session:
        active = await StrategyRepository(session).get_active_version(DEFAULT_STRATEGY_NAME)
        print('ACTIVE_v', active.version, StrategyWeights.from_db_columns(active).model_dump())
asyncio.run(main())
PY
