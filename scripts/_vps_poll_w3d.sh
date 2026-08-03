#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
echo "=== log tail ==="
tail -8 exports/top400_global_w_1h20_4h30_1d50_3d_run.log || true
if [[ -f exports/top400_global_w_1h20_4h30_1d50_3d.json ]]; then
  echo COMPLETE
  python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("exports/top400_global_w_1h20_4h30_1d50_3d.json").read_text())
print(json.dumps({"kpi": d.get("kpi_paper_book"), "ind": d.get("independent"), "cfg": {k: (d.get("config") or {}).get(k) for k in ("btc_regime_tfs","btc_regime_weights","capital")}}, indent=2))
PY
elif [[ -f exports/top400_paper_parity_90d.partial.json ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path("exports/top400_paper_parity_90d.partial.json").read_text())
print("PARTIAL", d.get("done"), "/", d.get("total"))
print(json.dumps(d.get("kpi_paper_book"), indent=2)[:1000])
PY
else
  echo NO_OUT
fi
pgrep -c -f 'run_top400_paper_parity' >/dev/null && echo RUNNING || echo NOT_RUNNING
