#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
mkdir -p exports
SINCE="${SINCE:-2026-07-31T16:32:35+00:00}"
WORKERS="${WORKERS:-3}"
LOG=/tmp/ab_btc_weights.log
: >"$LOG"

docker compose cp scripts/run_top400_paper_parity_90d.py worker:/tmp/run_top400_paper_parity_90d.py

run_one() {
  local preset="$1"
  local out="/tmp/top400_ab_${preset}.json"
  echo "===== START ${preset} $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" | tee -a "$LOG"
  docker compose exec -T worker python /tmp/run_top400_paper_parity_90d.py \
    --top 400 \
    --since "$SINCE" \
    --btc-weights "$preset" \
    --workers "$WORKERS" \
    --out "$out" \
    >>"$LOG" 2>&1
  local rc=$?
  echo "===== END ${preset} exit=${rc} $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" | tee -a "$LOG"
  if docker compose exec -T worker test -f "$out"; then
    docker compose cp "worker:${out}" "exports/top400_ab_${preset}.json"
  fi
  return "$rc"
}

run_one old
run_one new

python3 - <<'PY'
import json
from pathlib import Path

def load(p):
    d = json.loads(Path(p).read_text())
    k = d.get("kpi_paper_book") or {}
    i = d.get("independent") or {}
    return {
        "file": p,
        "label": d.get("label"),
        "window": d.get("window"),
        "weights": (d.get("config") or {}).get("btc_tf_weights"),
        "net": k.get("net_pnl"),
        "end_eq": k.get("end_equity"),
        "return_pct": k.get("return_pct"),
        "trades": k.get("closed"),
        "wr": k.get("win_rate"),
        "pf": k.get("profit_factor"),
        "dd": k.get("max_drawdown_pct"),
        "by_side": k.get("by_side"),
        "raw_trades": i.get("raw_trades"),
        "raw_net": i.get("raw_net_pnl"),
        "accepted": i.get("accepted_trades"),
        "skipped": i.get("skipped_by_caps"),
        "equity_daily": k.get("equity_daily"),
        "top_winners": (d.get("top_winners") or [])[:8],
        "top_losers": (d.get("top_losers") or [])[:8],
        "exits": k.get("exits"),
        "equity_curve_n": len(d.get("equity_curve") or []),
    }

old = load("exports/top400_ab_old.json")
new = load("exports/top400_ab_new.json")
cmp = {
    "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "delta_net": round(float(new["net"]) - float(old["net"]), 2),
    "delta_return_pct": round(float(new["return_pct"]) - float(old["return_pct"]), 2),
    "delta_trades": int(new["trades"]) - int(old["trades"]),
    "old": old,
    "new": new,
}
Path("exports/top400_ab_compare.json").write_text(json.dumps(cmp, indent=2), encoding="utf-8")
print(json.dumps(cmp, indent=2))
PY
