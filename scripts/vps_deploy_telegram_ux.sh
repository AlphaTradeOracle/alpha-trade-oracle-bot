#!/bin/bash
set -eu
SP=/opt/venv/lib/python3.12/site-packages/app
cd /opt/alpha-trade-oracle-bot

for f in formatting.py delivery.py notifier.py handlers.py; do
  tr -d '\r' < "/tmp/$f" > "/tmp/${f}.lf"
  docker cp "/tmp/${f}.lf" "alpha-trade-oracle-worker:${SP}/bot/$f"
  docker cp "/tmp/${f}.lf" "alpha-trade-oracle-worker:/app/app/bot/$f"
  docker cp "/tmp/${f}.lf" "alpha-trade-oracle-app:${SP}/bot/$f" 2>/dev/null || true
  docker cp "/tmp/${f}.lf" "alpha-trade-oracle-app:/app/app/bot/$f" 2>/dev/null || true
  cp "/tmp/${f}.lf" "app/bot/$f"
done

tr -d '\r' < /tmp/signal_chart.py > /tmp/signal_chart.py.lf
docker cp /tmp/signal_chart.py.lf "alpha-trade-oracle-worker:${SP}/charts/signal_chart.py"
docker cp /tmp/signal_chart.py.lf alpha-trade-oracle-worker:/app/app/charts/signal_chart.py
docker cp /tmp/signal_chart.py.lf "alpha-trade-oracle-app:${SP}/charts/signal_chart.py" 2>/dev/null || true
docker cp /tmp/signal_chart.py.lf alpha-trade-oracle-app:/app/app/charts/signal_chart.py 2>/dev/null || true
cp /tmp/signal_chart.py.lf app/charts/signal_chart.py

docker compose restart worker app
sleep 5
docker compose exec -T worker python -c 'from app.bot.formatting import format_signal_message; from app.charts.signal_chart import FIGURE_SIZE; print("ok", FIGURE_SIZE)'
