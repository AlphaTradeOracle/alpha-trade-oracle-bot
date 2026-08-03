#!/usr/bin/env bash
set -eu
echo "=== log ==="
grep -E '^RUN |^\{' /tmp/profit_delta_sims.log | tail -20 || true
echo "=== process ==="
pgrep -af '_vps_sim_profit_delta' || echo DONE
ps -eo etime,cmd | grep '_vps_sim_profit_delta.py' | grep -v grep || true
echo "=== json ==="
ls -la /tmp/profit_delta_sims.json 2>/dev/null || echo no_json_yet
# count completed scenario JSON lines
done_n=$(grep -c '"name": "sim_delta_' /tmp/profit_delta_sims.log 2>/dev/null || echo 0)
run_n=$(grep -c '^RUN ' /tmp/profit_delta_sims.log 2>/dev/null || echo 0)
echo "runs_started=$run_n results_printed≈ check below"
grep -c '^{' /tmp/profit_delta_sims.log || true
