#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
LOG=/tmp/sim_btc_short_veto.log
: >"$LOG"
sed -i 's/\r$//' /tmp/_vps_sim_btc_short_veto.py
docker compose cp /tmp/_vps_sim_btc_short_veto.py worker:/app/scripts/_vps_sim_btc_short_veto.py
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) btc short veto sim start =====" | tee -a "$LOG"
docker compose exec -T worker python /app/scripts/_vps_sim_btc_short_veto.py 2>&1 | tee -a "$LOG"
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) btc short veto sim done =====" | tee -a "$LOG"
