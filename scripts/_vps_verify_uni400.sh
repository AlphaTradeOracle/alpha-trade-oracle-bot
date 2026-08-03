#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
curl -fsS http://127.0.0.1:8000/health; echo
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_post_uni.json
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/tmp/desk_post_uni.json').read_text())
p=d.get('portfolio') or {}
r=d.get('marketRegime')
print('equity', p.get('equity'), 'closed', p.get('closedTrades'))
print('regime_present', r is not None)
if r:
    print('regime_bias', r.get('bias'), 'globalScore', r.get('globalScore'))
PY
docker compose exec -T postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -c \
  "SELECT count(*) FILTER (WHERE in_universe) AS in_universe FROM assets;"
