#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
CID=$(docker compose ps -q worker)
docker cp scripts/simulate_equity_levers.py "$CID:/app/scripts/simulate_equity_levers.py"
docker cp scripts/compare_paper_equity_combo.py "$CID:/app/scripts/compare_paper_equity_combo.py"
docker compose exec -T worker python -u /app/scripts/simulate_equity_levers.py --out /tmp/equity_levers.json
docker cp "$CID:/tmp/equity_levers.json" /tmp/equity_levers.json
ls -la /tmp/equity_levers.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/equity_levers.json'))
print('candidates', d['baseline']['fill_candidates'], d['baseline']['arm_stats'])
print('recommendation', json.dumps(d['recommendation'], indent=2))
print('toward_9k', d['toward_9k'])
print('--- scenarios ---')
for s in d['scenarios']:
    if 'hindsight' in s['name']:
        continue
    print(f"{s['name']:28} eq={s['end_equity']:8} n={s['accepted_n']:4} peak={s['peak_open']:3} wr={s['win_rate']:.0%}  {s['note']}")
PY
