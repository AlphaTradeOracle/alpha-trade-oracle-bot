#!/usr/bin/env bash
# Fehlende Batch-Backtests einzeln nachziehen (KuCoin, kein Persist).
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
    --no-persist 2>&1 | grep -E "Trades:|Trefferquote:|Netto|Profit Factor|Expectancy|Max. Drawdown|Sharpe|Sortino|Signale|FEHLER|fehlgeschlagen|Analyse nicht" || true
}

run_bt ETHUSDT 1h
run_bt SOLUSDT 1h
run_bt BTCUSDT 4h
run_bt ETHUSDT 4h
run_bt SOLUSDT 4h
run_bt BTCUSDT 15m
run_bt BTCUSDT 1d
