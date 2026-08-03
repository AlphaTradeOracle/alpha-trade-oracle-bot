#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
# Kill host-side wrappers
pkill -f '_vps_ab_btc_weights' 2>/dev/null || true
pkill -f 'ab_pipeline' 2>/dev/null || true
# Kill docker-compose exec clients
pkill -f 'run_top400_paper_parity' 2>/dev/null || true
# Kill inside worker
docker compose exec -T worker sh -c 'pkill -f run_top400_paper_parity || true; pkill -f multiprocessing || true' 2>/dev/null || true
sleep 2
echo "remaining:"
pgrep -af 'run_top400|_vps_ab|ab_pipeline' || echo STOPPED
echo "aborted $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a /tmp/ab_btc_weights.log
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot | python3 -c '
import sys, json
d = json.load(sys.stdin)
p = d.get("portfolio") or {}
print("paper_equity", p.get("equity"), "realized", p.get("realizedPnl"), "closed", p.get("closedTrades"))
'
