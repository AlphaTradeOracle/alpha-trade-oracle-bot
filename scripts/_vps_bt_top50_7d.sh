#!/usr/bin/env bash
# Companion: Top-50 × 7d for context if 24h is empty.
set -eu
cd /opt/alpha-trade-oracle-bot
OUT=/tmp/top50_7d_backtest.json
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  -e PYTHONUNBUFFERED=1 \
  worker python /app/scripts/run_top20_backtests.py \
    --top 50 --days 7 --timeframe 1h --capital 5000 --fee 0.05 --slippage 0.05 \
  >"$OUT" 2>/tmp/top50_7d_backtest.err
python3 - <<'PY'
import json
from pathlib import Path
raw=Path("/tmp/top50_7d_backtest.json").read_text(encoding="utf-8",errors="replace")
start=raw.rfind("\n{")
if start<0: start=raw.find("{")
d=json.loads(raw[start:])
Path("/tmp/top50_7d_backtest.clean.json").write_text(json.dumps(d,indent=2),encoding="utf-8")
s=d["summary"]
ok=[r for r in d["results"] if "error" not in r]
sig=sum(int(r.get("signals_generated") or 0) for r in ok)
print("7d SUMMARY", s, "signals_generated_sum", sig)
ok.sort(key=lambda r: float(r["overall"]["net_profit"]), reverse=True)
for r in ok[:8]:
    o=r["overall"]
    print(f"{r['symbol']:12} t={int(o['trade_count']):3} net={o['net_profit']:+8.2f} wr={o['win_rate']*100:4.0f}% pf={o['profit_factor']:5.2f} sig={r.get('signals_generated')}")
print("---")
for r in ok[-5:]:
    o=r["overall"]
    print(f"{r['symbol']:12} t={int(o['trade_count']):3} net={o['net_profit']:+8.2f}")
PY
tail -5 /tmp/top50_7d_backtest.err || true
