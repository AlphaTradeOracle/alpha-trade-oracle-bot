#!/usr/bin/env bash
# Deploy paper ledger FOR UPDATE lock + bind Postgres/Redis to localhost + enable UFW.
# Does NOT reset the paper ledger.
set -euo pipefail
APP=/opt/alpha-trade-oracle-bot
SRC=/tmp/lock_ufw_deploy
LOG=/tmp/deploy_lock_ufw.log
: >"$LOG"
cd "$APP"
dc() { docker compose -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"; }

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy lock+ufw =====" | tee -a "$LOG"

cp "$SRC/paper_repository.py" "$APP/app/repositories/paper_repository.py"
cp "$SRC/paper_trading_service.py" "$APP/app/services/paper_trading_service.py"
cp "$SRC/docker-compose.yml" "$APP/docker-compose.yml"

# Recreate DB/cache with localhost bind; recreate app/worker with new code.
dc up -d --force-recreate postgres redis >/dev/null
sleep 5
dc up -d --force-recreate --no-deps app worker >/dev/null
sleep 8
for c in alpha-trade-oracle-worker alpha-trade-oracle-app; do
  docker cp "$APP/app/repositories/paper_repository.py" "$c:/app/app/repositories/paper_repository.py"
  docker cp "$APP/app/services/paper_trading_service.py" "$c:/app/app/services/paper_trading_service.py"
done
dc restart worker app >/dev/null
sleep 8

dc exec -T worker python - <<'PY' | tee -a "$LOG"
from app.repositories.paper_repository import PaperRepository
import inspect
src = inspect.getsource(PaperRepository.lock_account)
assert "with_for_update" in src
print("LOCK_OK")
PY

# Port bind check: postgres/redis must listen on 127.0.0.1 only (host side).
ss -lntup | grep -E ':(5432|6379|8000)\b' | tee -a "$LOG" || true
python3 - <<'PY' | tee -a "$LOG"
import subprocess
out = subprocess.check_output(["ss", "-lnt"], text=True)
bad = []
for port in ("5432", "6379"):
    for line in out.splitlines():
        if f":{port}" not in line:
            continue
        # Expect 127.0.0.1:PORT or [::1]:PORT — fail on 0.0.0.0 / *
        if "127.0.0.1:" + port in line or "[::1]:" + port in line:
            continue
        if "*:" + port in line or "0.0.0.0:" + port in line or "[::]:" + port in line:
            bad.append(line.strip())
if bad:
    raise SystemExit("PUBLIC_BIND_FAIL\n" + "\n".join(bad))
print("BIND_OK")
PY

curl -fsS http://127.0.0.1:8000/health | tee -a "$LOG"
echo | tee -a "$LOG"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_lock.json
python3 - <<'PY' | tee -a "$LOG"
import json
from pathlib import Path
p=json.loads(Path("/tmp/desk_lock.json").read_text()).get("portfolio") or {}
print("desk", {k:p.get(k) for k in ["equity","cash","openPositions","pendingOrders","closedTrades"]})
PY

# UFW: SSH + HTTP/S only. Enable only after rules are in place.
if command -v ufw >/dev/null 2>&1; then
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null || ufw allow 22/tcp >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
  ufw status verbose | tee -a "$LOG"
  echo "UFW_OK" | tee -a "$LOG"
else
  echo "UFW_MISSING" | tee -a "$LOG"
  exit 1
fi

# External reachability probe from the host itself via public IP is optional;
# confirm docker publishes only loopback for DB ports.
echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) deploy lock+ufw DONE =====" | tee -a "$LOG"
