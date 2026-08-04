#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "=== compose ==="
docker compose ps

echo "=== local ==="
curl -sS -o /dev/null -w "local_health=%{http_code}\n" http://127.0.0.1:8000/health
curl -sS -o /dev/null -w "local_desk=%{http_code}\n" http://127.0.0.1:8000/api/v1/desk/snapshot
curl -sS http://127.0.0.1:8000/api/v1/desk/snapshot > /tmp/desk_local.json
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("/tmp/desk_local.json").read_text())
print("desk_top_keys", sorted(d.keys())[:20])
for key in ("paper", "account", "performance", "equity", "updated_at", "generated_at", "snapshot_at"):
    if key in d:
        val = d[key]
        if isinstance(val, dict):
            print(key, {k: val.get(k) for k in list(val)[:10]})
        else:
            print(key, val)
paper = d.get("paper") or d.get("paper_account") or {}
if isinstance(paper, dict):
    for k in ("equity", "balance", "realized_pnl", "open_positions", "closed_trades", "win_rate", "name", "account_id"):
        if k in paper:
            print("paper." + k, paper[k])
# nested common shapes
for path in (
    ("stats",),
    ("kpi",),
    ("ledger",),
):
    cur = d
    ok = True
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            ok = False
            break
    if ok:
        print(".".join(path), cur if not isinstance(cur, dict) else list(cur)[:8])
PY

echo "=== public ==="
curl -sS -o /dev/null -w "site=%{http_code}\n" --max-time 20 https://alpha-trade-oracle.com/ || echo site=FAIL
curl -sS -o /dev/null -w "pub_health=%{http_code}\n" --max-time 20 https://alpha-trade-oracle.com/health || echo pub_health=FAIL
curl -sS -o /dev/null -w "pub_desk=%{http_code}\n" --max-time 20 https://alpha-trade-oracle.com/api/v1/desk/snapshot || echo pub_desk=FAIL

echo "=== sync desk snapshot (worker) ==="
docker compose exec -T worker python scripts/sync_desk_snapshot.py 2>&1 | tail -n 40 || \
  docker compose exec -T worker python -m app.cli desk sync 2>&1 | tail -n 40 || \
  echo "no sync command; relying on live API"

echo "=== public desk after sync ==="
curl -sS --max-time 20 https://alpha-trade-oracle.com/api/v1/desk/snapshot > /tmp/desk_pub.json || true
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/tmp/desk_pub.json")
if not p.exists() or not p.read_text().strip():
    print("public_desk empty")
else:
    d = json.loads(p.read_text())
    print("pub_keys", sorted(d.keys())[:20])
    paper = d.get("paper") or d.get("paper_account") or {}
    if isinstance(paper, dict):
        for k in ("equity", "balance", "realized_pnl", "open_positions", "closed_trades", "win_rate"):
            if k in paper:
                print("pub.paper." + k, paper[k])
PY
