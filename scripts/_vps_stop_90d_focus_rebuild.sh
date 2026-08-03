#!/usr/bin/env bash
set -euo pipefail

echo "=== stop 90d top400 backtest ==="
pgrep -af 'top400_paper_parity|run_top400' || true
pkill -f 'run_top400_paper_parity_90d' 2>/dev/null || true
pkill -f 'top400_paper_parity_90d' 2>/dev/null || true

# Stop any docker containers whose command mentions top400
while read -r id; do
  [[ -z "$id" ]] && continue
  cmd="$(docker inspect -f '{{json .Config.Cmd}} {{.Name}}' "$id" 2>/dev/null || true)"
  if echo "$cmd" | grep -qi top400; then
    echo "stopping container $id $cmd"
    docker stop "$id" >/dev/null || true
  fi
done < <(docker ps -q)

# Also stop the long-running compose run started earlier if still alive
pgrep -af 'run_top400_paper_parity_90d' || echo "no top400 procs left"

echo
echo "=== trendline / paper rebuild status ==="
pgrep -af 'paper rebuild|deploy_trendline|_vps_deploy_trendline' || echo "no rebuild procs"
echo "--- log tail ---"
tail -40 /tmp/deploy_trendline_rebuild.log 2>/dev/null || echo "no deploy log"
echo "--- markers ---"
grep -E 'AFTER|DESK|rebuild done|Traceback|BASELINE|verify trendline|OK$|paper rebuild \(' /tmp/deploy_trendline_rebuild.log 2>/dev/null | tail -40 || true
