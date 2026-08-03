#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot

docker compose run --rm --no-deps -v /tmp:/tmp worker \
  python /app/scripts/vps_audit_desk_values.py --out /tmp/desk_math_now.json \
  > /tmp/dm_now.out 2>/tmp/dm_now.err || true

python3 - <<'PY'
import json
from pathlib import Path
p = Path("/tmp/desk_math_now.json")
print("desk_math_now_exists", p.exists(), "mtime", p.stat().st_mtime if p.exists() else None)
if p.exists():
    d = json.loads(p.read_text())
    print(json.dumps({
        "final_ok": d.get("final_ok"),
        "issues": d.get("issues"),
        "warnings": d.get("warnings"),
        "portfolio": d.get("portfolio"),
        "pending_n": d.get("pending_n"),
        "closed_n": d.get("closed_n"),
        "trade_ok": d.get("trade_ok"),
        "trade_fail": d.get("trade_fail"),
        "pending_symbols": [x.get("symbol") for x in (d.get("pending_checks") or [])],
    }, indent=2))
else:
    print(Path("/tmp/dm_now.out").read_text(errors="replace")[-2000:])
    print(Path("/tmp/dm_now.err").read_text(errors="replace")[-1500:])
PY

set -a; . ./.env; set +a
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SELECT job_key, last_status, last_run_at, last_success_at, next_run_at,
       LEFT(COALESCE(last_error,''),120) AS err
FROM scheduled_jobs
WHERE job_key LIKE '%scan%' OR job_key LIKE '%paper%'
ORDER BY job_key;

SELECT id, symbol, status, ROUND(entry_price::numeric,8) AS entry,
       ROUND(stop_loss::numeric,8) AS stop, opened_at,
       LEFT(notes,80) AS notes
FROM paper_positions
WHERE account_id=1 AND status='pending'
ORDER BY opened_at DESC;

SELECT
  COUNT(*) FILTER (WHERE status='closed') AS closed,
  COUNT(*) FILTER (WHERE status='closed' AND realized_pnl>0) AS wins,
  COUNT(*) FILTER (WHERE status='closed' AND realized_pnl<=0) AS losses,
  COUNT(*) FILTER (WHERE status='pending') AS pending,
  COUNT(*) FILTER (WHERE status='open') AS open,
  COUNT(*) FILTER (WHERE status='cancelled') AS cancelled
FROM paper_positions WHERE account_id=1;

SELECT ROUND(cash_balance::numeric,2), ROUND(realized_pnl::numeric,2)
FROM paper_accounts WHERE id=1;
SQL

docker compose logs worker --tail 40
