#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
mkdir -p exports
OUT="exports/top400_48h_$(date -u +%Y%m%dT%H%M%SZ).json"
LOG=/tmp/top400_48h_run.log

docker compose cp scripts/run_top400_paper_parity_90d.py worker:/tmp/run_top400_paper_parity_90d.py
set +e
docker compose exec -T worker python /tmp/run_top400_paper_parity_90d.py \
  --top 400 \
  --days 2 \
  --workers 3 \
  --out /tmp/top400_48h.json \
  >"$LOG" 2>&1
rc=$?
set -e
echo "exit=$rc" | tee -a "$LOG"
tail -n 80 "$LOG"

if docker compose exec -T worker test -f /tmp/top400_48h.json; then
  docker compose cp worker:/tmp/top400_48h.json "$OUT"
  # also pull partial if present
  docker compose cp worker:/tmp/top400_paper_parity_90d.partial.json \
    exports/top400_48h.partial.json 2>/dev/null || true
  echo "OUT=$OUT"
  python3 - <<PY
import json
from pathlib import Path
p = Path("$OUT")
d = json.loads(p.read_text())
print("file", p)
print("window", d.get("window"))
print("independent", json.dumps(d.get("independent"), indent=2))
kpi = d.get("kpi_paper_book") or {}
print("kpi_net", kpi.get("net_pnl"), "trades", kpi.get("closed"), "wr", kpi.get("win_rate"),
      "end_eq", kpi.get("end_equity"), "dd", kpi.get("max_drawdown_pct"))
print("equity_points", len(d.get("equity_curve") or []))
print("top_winners", d.get("top_winners", [])[:5])
print("top_losers", d.get("top_losers", [])[:5])
PY
else
  echo "MISSING_OUTPUT"
  exit 1
fi
