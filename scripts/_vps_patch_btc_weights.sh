#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
python3 - <<'PY'
from pathlib import Path
p = Path('app/market_regime/bitcoin.py')
text = p.read_text()
old = '''DEFAULT_TF_WEIGHTS: dict[str, float] = {
    "1w": 0.35,
    "1d": 0.30,
    "4h": 0.25,
    "1h": 0.10,
    "12h": 0.20,
    "15m": 0.05,
}'''
new = '''DEFAULT_TF_WEIGHTS: dict[str, float] = {
    "1w": 0.10,
    "1d": 0.15,
    "4h": 0.35,
    "1h": 0.40,
    "12h": 0.20,
    "15m": 0.05,
}'''
if old not in text:
    if '"1h": 0.40' in text and '"1w": 0.10' in text:
        print('already_patched')
    else:
        raise SystemExit('pattern_not_found')
else:
    # keep docstring update optional; weights are the functional change
    p.write_text(text.replace(old, new, 1))
    print('patched')
PY
docker compose up -d --build --no-deps worker app
# wait health
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    echo health_ok
    break
  fi
  sleep 2
done
bash /tmp/_vps_regime_btc_tfs.sh
