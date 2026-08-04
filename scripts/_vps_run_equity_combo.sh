#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot
CID=$(docker compose ps -q worker)
docker cp scripts/compare_paper_equity_combo.py "$CID:/app/scripts/compare_paper_equity_combo.py"
docker compose exec -T worker python -u /app/scripts/compare_paper_equity_combo.py --out /tmp/paper_equity_combo.json
docker cp "$CID:/tmp/paper_equity_combo.json" /tmp/paper_equity_combo.json
ls -la /tmp/paper_equity_combo.json
python3 - <<'PY'
import json
d=json.load(open('/tmp/paper_equity_combo.json'))
print('LIVE', d['live']['end_equity'], d['live']['return_pct'], '%')
u=d.get('combo_uncapped') or d.get('combo')
c=d.get('combo_capped') or {}
print('UNCAPPED', u.get('end_equity'), u.get('return_pct'), '%', 'skip_net', u.get('skip_fills_net'))
print('CAPPED', c.get('end_equity'), c.get('return_pct'), '%')
print('  accepted', c.get('accepted_n'), 'skipped_caps', c.get('skipped_n'), c.get('skip_reasons'))
print('  skip_fills_accepted', c.get('skip_fills_accepted'), 'skip_net', c.get('skip_fills_net'))
print('DELTA_CAPPED', d.get('delta_capped_vs_live'))
print('aligned', d.get('aligned_daily'))
PY
