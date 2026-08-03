#!/usr/bin/env bash
# Sim-only: rescore 18/18 + soft blend + paper rebuild on sim account.
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/sim_weights_18_18.log
: >"$LOG"

sed -i 's/\r$//' /tmp/_vps_sim_weights_18_18.py
docker compose cp /tmp/_vps_sim_weights_18_18.py worker:/app/scripts/_vps_sim_weights_18_18.py
# ensure soft-blend helper is present in image path
if [[ -f scripts/rescore_signals_regime_soft_blend.py ]]; then
  docker compose cp scripts/rescore_signals_regime_soft_blend.py worker:/app/scripts/rescore_signals_regime_soft_blend.py || true
fi

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) sim 18/18 start =====" | tee -a "$LOG"
docker compose exec -T worker python /app/scripts/_vps_sim_weights_18_18.py 2>&1 | tee -a "$LOG"
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) sim 18/18 done =====" | tee -a "$LOG"
