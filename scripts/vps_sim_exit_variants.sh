#!/bin/bash
set -eu
docker cp /tmp/simulate_exit_variants.py alpha-trade-oracle-worker:/app/scripts/simulate_exit_variants.py
cd /opt/alpha-trade-oracle-bot
docker compose exec -T worker python scripts/simulate_exit_variants.py > /tmp/exit_variants_out.json 2> /tmp/exit_variants_err.log
echo DONE
cat /tmp/exit_variants_err.log
python3 <<'PY'
import json
raw=open("/tmp/exit_variants_out.json",encoding="utf-8").read()
d=json.loads(raw[raw.find("{"):])
print("WINNER", d["winner"])
for s in d["ranking"]:
    print(f"{s['mode']:18} total={s['total_pnl']:+8.2f}  wins={s['wins']} losses={s['losses']}")
    for row in s["per_symbol"][:3]:
        print(f"   top {row['symbol']}: {row['pnl']:+.2f} ({row['exit']})")
PY
