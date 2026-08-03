#!/usr/bin/env bash
set -eu
cd /opt/alpha-trade-oracle-bot
set -a; source .env; set +a

echo "===== HOTFIX IN IMAGE? ====="
docker compose exec -T worker python - <<'PY'
from app.repositories.asset_repository import prepare_indicator_snapshot_values
vals = prepare_indicator_snapshot_values({
    "close_price": 1.0,
    "falling_resistance": 2.0,
    "rising_support": 0.5,
})
assert "falling_resistance" not in vals
assert vals["extra_values"]["falling_resistance"] == 2.0
print("HOTFIX_OK", vals["extra_values"])
PY

echo "===== PAPER VERIFY ====="
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  worker python /app/scripts/vps_verify_paper_since_reset.py \
  >/tmp/paper_verify_raw.txt 2>/tmp/paper_verify_err.txt || true
python3 - <<'PY'
from pathlib import Path
import json
raw = Path("/tmp/paper_verify_raw.txt").read_text(encoding="utf-8", errors="replace")
# find last JSON object
idx = raw.rfind("\n{")
if idx < 0:
    idx = raw.find("{")
blob = raw[idx:].strip()
# trim after first top-level object
depth=0; end=None
for i,ch in enumerate(blob):
    if ch=='{': depth+=1
    elif ch=='}':
        depth-=1
        if depth==0:
            end=i+1; break
d=json.loads(blob[:end])
Path("/tmp/paper_verify.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
v=d.get("verdict") or {}
acct=d.get("account") or {}
print(json.dumps({
    "FINAL_OK": v.get("FINAL_OK"),
    "missing_fills": v.get("missing_fills"),
    "extra_fills": v.get("extra_fills"),
    "geometry_mismatches": v.get("geometry_mismatches"),
    "status_mismatches": v.get("status_mismatches"),
    "should_have_traded": v.get("should_have_traded"),
    "traded_ok": v.get("traded_ok"),
    "expected_fills": v.get("expected_fills"),
    "correctly_skipped": v.get("correctly_skipped"),
    "cash": acct.get("cash"),
    "realized": acct.get("realized"),
    "skip_reasons": v.get("skip_reasons") or d.get("skip_reasons"),
}, indent=2, default=str))
err=Path("/tmp/paper_verify_err.txt").read_text(encoding="utf-8", errors="replace")
if err.strip():
    print("ERR_TAIL", err[-800:])
PY

echo "===== DESK MATH ====="
docker compose run --rm --no-deps \
  -v /opt/alpha-trade-oracle-bot/scripts:/app/scripts \
  -v /tmp:/tmp \
  worker python /app/scripts/vps_audit_desk_values.py --out /tmp/desk_math_audit.json \
  >/tmp/desk_math_raw.txt 2>/tmp/desk_math_err.txt || true
python3 - <<'PY'
import json
from pathlib import Path
p=Path("/tmp/desk_math_audit.json")
if not p.exists():
    print("MISSING", Path("/tmp/desk_math_raw.txt").read_text(encoding="utf-8", errors="replace")[-1500:])
    print("ERR", Path("/tmp/desk_math_err.txt").read_text(encoding="utf-8", errors="replace")[-1500:])
else:
    d=json.loads(p.read_text())
    print(json.dumps({
        "final_ok": d.get("final_ok"),
        "issues": d.get("issues"),
        "warnings": d.get("warnings"),
        "trade_ok": d.get("trade_ok"),
        "trade_fail": d.get("trade_fail"),
        "portfolio": d.get("portfolio"),
        "open_n": d.get("open_n"),
        "closed_n": d.get("closed_n"),
        "pending_n": d.get("pending_n"),
    }, indent=2, default=str))
PY

echo "===== DESK API + PUBLIC ====="
curl -fsS --max-time 20 http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_snap.json
curl -fsS --max-time 20 https://alpha-trade-oracle.com/api/v1/desk/snapshot -o /tmp/desk_pub.json || true
curl -fsS -o /dev/null -w "local_health=%{http_code}\n" http://127.0.0.1:8000/health
curl -fsS -o /dev/null -w "public_health=%{http_code}\n" https://alpha-trade-oracle.com/health || echo public_health=FAIL
python3 - <<'PY'
import json
from pathlib import Path
local=json.loads(Path("/tmp/desk_snap.json").read_text())
pub=json.loads(Path("/tmp/desk_pub.json").read_text()) if Path("/tmp/desk_pub.json").exists() else {}
lp=local.get("portfolio") or {}
pp=pub.get("portfolio") or {}
trades=local.get("trades") or []
print(json.dumps({
    "local": {k: lp.get(k) for k in ["equity","cash","realizedPnl","openPositions","pendingOrders","closedTrades","winRatePct","totalReturnPct","profitFactor"]},
    "public": {k: pp.get(k) for k in ["equity","cash","realizedPnl","openPositions","pendingOrders","closedTrades","winRatePct"]},
    "trade_n": len(trades),
    "symbols": [t.get("symbol") for t in trades[:25]],
}, indent=2))
# cash/equity identity
diffs=[]
for k in ["equity","cash","realizedPnl","closedTrades","openPositions","pendingOrders"]:
    a,b=lp.get(k),pp.get(k)
    try:
        if a is not None and b is not None and abs(float(a)-float(b))>=0.02:
            diffs.append({k:[a,b]})
    except Exception:
        if a!=b: diffs.append({k:[a,b]})
print("PUBLIC_DIFFS", diffs)
PY

echo "===== DB LEDGER ====="
docker compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A <<'SQL'
SELECT 'status|'||status||'|'||COUNT(*)||'|'||COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions WHERE account_id=1 GROUP BY status ORDER BY status;
SELECT 'acct|'||ROUND(cash_balance::numeric,2)||'|'||ROUND(realized_pnl::numeric,2)||'|'||ROUND(initial_balance::numeric,2)
FROM paper_accounts WHERE id=1;
SELECT 'fills_notes_ok|'||COUNT(*) FROM paper_positions
WHERE account_id=1 AND status='closed' AND notes ILIKE '%retest_filled%';
SELECT 'closed_no_exit|'||COUNT(*) FROM paper_positions
WHERE account_id=1 AND status='closed' AND (exit_reason IS NULL OR exit_reason='');
SELECT 'signals_24h|'||COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours';
SELECT 'tradeable_24h|'||COUNT(*) FROM signals
WHERE created_at > NOW() - INTERVAL '24 hours'
  AND direction IN ('LONG','SHORT','STRONG_LONG','STRONG_SHORT');
SELECT 'dispatched_24h|'||COUNT(*) FROM signals
WHERE created_at > NOW() - INTERVAL '24 hours' AND is_dispatched=true;
SELECT 'tl_skips|'||COUNT(*) FROM paper_positions
WHERE account_id=1 AND notes ILIKE '%broke_%';
SQL

echo "===== RECENT WORKER ERRORS ====="
docker logs alpha-trade-oracle-worker --since 10m 2>&1 \
  | grep -E 'Unconsumed column|scan_symbol_error' | tail -20 || echo NONE

echo "===== DONE ====="
