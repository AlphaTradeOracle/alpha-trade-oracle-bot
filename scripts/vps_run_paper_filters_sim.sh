#!/usr/bin/env bash
# Run paper filter counterfactual sweep on VPS and write JSON export.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

docker cp scripts/simulate_paper_filters.py alpha-trade-oracle-worker:/app/scripts/simulate_paper_filters.py
docker exec alpha-trade-oracle-worker python -m scripts.simulate_paper_filters \
  > exports/paper_filters_sim.json 2>exports/paper_filters_sim.log

echo "Wrote exports/paper_filters_sim.json"
tail -3 exports/paper_filters_sim.log
