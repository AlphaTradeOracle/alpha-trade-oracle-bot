#!/usr/bin/env bash
# Top-50 × last 24h backtest with live strategy settings. Capital $5000/symbol.
set -eu
cd /opt/alpha-trade-oracle-bot

OUT=/tmp/top50_24h_backtest.json
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) TOP50 24h BACKTEST ====="
echo "HEAD=$(git rev-parse --short HEAD)"
grep -E '^SIGNAL_SHORT_MAX|^SIGNAL_MIN_SCORE|^SIGNAL_SHORT_MIN|^PAPER_RETEST_ENTRY|^PAPER_FEE|^PAPER_INITIAL|^SIGNAL_MIN_ADX|^SIGNAL_TRENDLINE_GATE' .env || true

docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  -e PYTHONUNBUFFERED=1 \
  worker python /app/scripts/run_top20_backtests.py \
    --top 50 \
    --days 1 \
    --timeframe 1h \
    --capital 5000 \
    --fee 0.05 \
    --slippage 0.05 \
  >"$OUT" 2>/tmp/top50_24h_backtest.err

echo "EXIT=$?"
python3 - <<'PY'
import json
from pathlib import Path
raw = Path("/tmp/top50_24h_backtest.json").read_text(encoding="utf-8", errors="replace")
# stdout may mix logs — find last JSON object
start = raw.rfind("\n{")
if start < 0:
    start = raw.find("{")
d = json.loads(raw[start:])
Path("/tmp/top50_24h_backtest.clean.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
s = d.get("summary") or {}
print("SUMMARY", json.dumps(s, indent=2))
print("GATES", json.dumps(d.get("gates"), indent=2))
print("WINDOW", d.get("start"), "→", d.get("end"), "tf", d.get("timeframe"), "mtf", d.get("use_multi_timeframe"))
ok = [r for r in d.get("results") or [] if "error" not in r]
ok.sort(key=lambda r: float(r["overall"]["net_profit"]), reverse=True)
print("TOP5")
for r in ok[:5]:
    o=r["overall"]
    print(f"  {r['symbol']:12} trades={int(o['trade_count']):3} net={o['net_profit']:+.2f} wr={o['win_rate']*100:.0f}% pf={o['profit_factor']:.2f}")
print("BOTTOM5")
for r in ok[-5:]:
    o=r["overall"]
    print(f"  {r['symbol']:12} trades={int(o['trade_count']):3} net={o['net_profit']:+.2f} wr={o['win_rate']*100:.0f}% pf={o['profit_factor']:.2f}")
failed=[r for r in d.get("results") or [] if "error" in r]
print("FAILED", len(failed), [r.get("symbol") for r in failed[:10]])
PY
echo "ERR_TAIL:"
tail -30 /tmp/top50_24h_backtest.err || true
echo "===== DONE ====="
