#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_regime.json
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/tmp/desk_regime.json').read_text())
r=d.get('marketRegime') or {}
print('bias', r.get('bias'))
print('globalScore', r.get('globalScore'))
print('hardVeto', r.get('hardVeto'))
print('available', r.get('available'))
btc=r.get('btc') or {}
print('btc.bias', btc.get('bias'), 'btc.score', btc.get('score'), 'btc.trend', btc.get('trend'))
tfs=btc.get('timeframes') or btc.get('timeframeBias') or {}
if isinstance(tfs, dict):
    for tf, snap in tfs.items():
        if isinstance(snap, dict):
            print(f"  tf {tf}: bias={snap.get('bias')} score={snap.get('score')} trend={snap.get('trend')}")
        else:
            print(f"  tf {tf}: {snap}")
print('detail', (r.get('detail') or '')[:300])
PY
grep -E '^MARKET_REGIME|^REGIME_FILTER|^MARKET_SCORE' .env || true
