#!/usr/bin/env bash
set -euo pipefail
LOG=/opt/alpha-trade-oracle-bot/exports/short_min_floor_7d_top50.log
JSON=/opt/alpha-trade-oracle-bot/exports/short_min_floor_7d_top50.json

for i in $(seq 1 90); do
  if [[ -f "$JSON" ]]; then
    echo DONE
    break
  fi
  if ! pgrep -f 'backtest_short_min_floor.py' >/dev/null; then
    echo DIED
    break
  fi
  step=$(grep -oE '\[[0-9]+/300\]' "$LOG" 2>/dev/null | tail -1 || true)
  echo "$(date -u +%H:%M:%S) $step"
  sleep 30
done

if [[ -f "$JSON" ]]; then
  python3 - <<'PY'
import json
p=json.load(open("/opt/alpha-trade-oracle-bot/exports/short_min_floor_7d_top50.json"))
print("VERDICT", p.get("verdict"))
for s in p["summaries"]:
    print(
        f"min>{s['short_min']:g} shorts={s['short_trades']} "
        f"net={s['short_net']} wr={s['short_wr']}% "
        f"d18_net={s['delta_short_net_vs_18']} d18_n={s['delta_short_trades_vs_18']}"
    )
print("MARGINAL", p["marginal_vs_floor_18"])
print("WINDOW", p["window"])
print(json.dumps(p["summaries"]))
PY
  exit 0
fi

echo FAIL_NO_JSON
tail -40 "$LOG" || true
exit 1
