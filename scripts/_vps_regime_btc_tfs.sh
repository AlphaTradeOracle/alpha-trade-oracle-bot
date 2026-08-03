#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python - <<'PY'
import asyncio
from app.container import build_container
from app.core.config import get_settings
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.market_regime.score import FinalScoreCalculator
from app.market_regime.types import ScoreWeights
from app.market_regime.adapter import hard_veto_reason, bias_to_market_regime

async def main():
    configure_logging('WARNING', json_output=False)
    s = get_settings()
    c = build_container(s)
    snap = await c.analysis_service.resolve_market_regime_snapshot(refresh=True)
    print('bias', snap.bias.value)
    print('global', round(snap.global_score, 2))
    print('available', snap.available)
    b = snap.btc
    print('btc.bias', b.bias.value, 'btc.score', round(b.score, 2), 'btc.trend', b.trend)
    for tf, t in sorted((b.timeframes or {}).items(), key=lambda x: {'1w':0,'1d':1,'4h':2,'1h':3}.get(x[0],9)):
        print(f'  {tf}: score={t.score:.1f} bias={t.bias.value} trend={t.trend}')
    print('legacy_regime', bias_to_market_regime(snap.bias))
    print('hard_veto_short', hard_veto_reason(snap, SignalDirection.SHORT, enabled=True))
    print('hard_veto_long', hard_veto_reason(snap, SignalDirection.LONG, enabled=True))
    calc = FinalScoreCalculator(ScoreWeights(
        coin=float(s.market_score_weight_coin),
        global_market=float(s.market_score_weight_global),
        funding=float(s.market_score_weight_funding),
        open_interest=float(s.market_score_weight_oi),
        liquidations=float(s.market_score_weight_liquidations),
    ))
    for coin in (18, 22, 28, 35):
        blended = calc.blend(coin, SignalDirection.SHORT, snap)
        print(f'blend short coin={coin} -> final={blended.final_score}')
    await c.aclose()

asyncio.run(main())
PY
