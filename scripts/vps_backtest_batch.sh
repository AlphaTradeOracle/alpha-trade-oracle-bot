#!/usr/bin/env bash
# Batch-Backtests fuer Strategie-Review (KuCoin, kein Persist).
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

START="${START:-2023-07-01}"
END="${END:-2025-07-01}"

run_bt() {
  local symbol="$1"
  local tf="$2"
  echo ""
  echo "========== ${symbol} ${tf} =========="
  docker compose exec -T worker python -m app.cli backtest \
    --symbol "$symbol" \
    --timeframe "$tf" \
    --start "$START" \
    --end "$END" \
    --no-persist 2>&1 | grep -E "Trades:|Trefferquote:|Netto|Profit|Drawdown|Sharpe|Sortino|Profit Factor|Expectancy|Signale|uebersprungen|FEHLER|Analyse nicht" || true
}

for tf in 1h 4h; do
  run_bt BTCUSDT "$tf"
  run_bt ETHUSDT "$tf"
  run_bt SOLUSDT "$tf"
done

run_bt BTCUSDT 15m
run_bt BTCUSDT 1d
