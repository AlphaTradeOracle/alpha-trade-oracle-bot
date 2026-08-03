#!/usr/bin/env bash
# Full post-trendline paper audit since reset.
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) post-TL paper audit ====="
git log -1 --oneline
grep -E '^SIGNAL_TRENDLINE_' .env || true

echo "==> verify_paper_since_reset"
docker compose run --rm --no-deps worker \
  python /app/scripts/vps_verify_paper_since_reset.py \
  > /tmp/paper_verify.json 2>/tmp/paper_verify.err || true

python3 <<'PY'
import json, re
from pathlib import Path
from collections import Counter

raw = Path("/tmp/paper_verify.json").read_text(encoding="utf-8", errors="replace").strip()
start = raw.rfind("{")
d = json.loads(raw[start:]) if start >= 0 else {}
Path("/tmp/paper_verify_parsed.json").write_text(json.dumps(d, indent=2), encoding="utf-8")

print("FINAL_OK", d.get("FINAL_OK"))
print("summary", json.dumps(d.get("summary") or d.get("verdict") or {}, indent=2, default=str)[:3000])

diffs = d.get("diffs") or {}
for key in ("missing_fills", "extra_fills", "geometry_mismatch", "geometry_mismatches", "status_mismatch", "pnl_mismatch", "pnl_mismatches"):
    val = diffs.get(key) or d.get(key) or []
    if isinstance(val, list):
        print(f"{key}_n", len(val))
        if val:
            print(json.dumps(val[:8], indent=2, default=str)[:2500])

audit = d.get("signal_audit") or {}
print("qualifying", audit.get("qualifying"))
print("traded_ok", len(audit.get("traded_ok") or []))
print("correctly_skipped", len(audit.get("correctly_skipped") or []))
print("should_have_traded", len(audit.get("should_have_traded") or []))
if audit.get("should_have_traded"):
    print(json.dumps(audit["should_have_traded"][:10], indent=2, default=str)[:2000])

skips = (d.get("expected_stream") or {}).get("skipped") or []
reasons = Counter((x.get("reason") or "?") for x in skips)
print("skip_reasons", dict(reasons))
tl = [x for x in skips if "trendline" in str(x.get("reason") or "") or "broke_" in str(x.get("note") or "")]
print("trendline_skips_expected", len(tl))
for x in tl[:12]:
    print(" TL", x.get("symbol"), x.get("reason"), (x.get("note") or "")[:90])

filled = (d.get("expected_stream") or {}).get("filled") or []
print("expected_fills", len(filled))
print("db_book", json.dumps(d.get("db_book") or {}, indent=2, default=str)[:1500])
print("account", json.dumps(d.get("account") or {}, indent=2, default=str)[:800])
err = Path("/tmp/paper_verify.err").read_text(encoding="utf-8", errors="replace")
if "Traceback" in err or "Error" in err[-500:]:
    print("ERR_TAIL", err[-2000:])
PY

echo "==> ledger SQL checks"
PGPASSWORD=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)
export PGPASSWORD
docker exec -e PGPASSWORD -i alpha-trade-oracle-postgres \
  psql -U alpha_trade_oracle -d alpha_trade_oracle <<'SQL'
\pset format aligned
SELECT 'ACCT' AS k, round(a.realized_pnl::numeric,2) AS realized, round(a.cash_balance::numeric,2) AS cash,
       round(a.initial_balance::numeric,2) AS initial
FROM paper_accounts a WHERE a.name='default';

SELECT p.status, count(*) AS n, round(coalesce(sum(p.realized_pnl),0)::numeric,2) AS pnl
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' GROUP BY 1 ORDER BY 1;

-- Fill note presence for closed/open
SELECT
  count(*) FILTER (WHERE p.status IN ('closed','open') AND p.notes ILIKE '%retest_filled%') AS filled_with_note,
  count(*) FILTER (WHERE p.status IN ('closed','open') AND (p.notes IS NULL OR p.notes NOT ILIKE '%retest_filled%')) AS filled_without_note,
  count(*) FILTER (WHERE p.status='cancelled' AND p.notes ILIKE '%broke_falling%') AS tl_fall_skips,
  count(*) FILTER (WHERE p.status='cancelled' AND p.notes ILIKE '%broke_rising%') AS tl_rise_skips,
  count(*) FILTER (WHERE p.status='cancelled' AND p.exit_reason='retest_skipped') AS retest_skipped_n
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default';

-- Closed trades: exit sanity
SELECT p.symbol, p.direction, round(p.realized_pnl::numeric,2) AS pnl, p.exit_reason,
       p.opened_at::text, p.closed_at::text,
       CASE WHEN p.closed_at IS NULL THEN 'MISSING_CLOSED_AT'
            WHEN p.opened_at IS NOT NULL AND p.closed_at < p.opened_at THEN 'CLOSED_BEFORE_OPEN'
            ELSE 'OK' END AS time_ok,
       CASE WHEN p.entry_price IS NULL OR p.entry_price <= 0 THEN 'BAD_ENTRY'
            WHEN p.current_stop IS NULL THEN 'BAD_STOP'
            ELSE 'OK' END AS levels_ok
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default' AND p.status='closed'
ORDER BY p.opened_at;

-- Orphan fills / fill pnl sum vs position
SELECT 'FILLS' AS k, count(*) AS n, round(coalesce(sum(f.pnl),0)::numeric,2) AS fill_pnl,
       round(coalesce(sum(f.fee),0)::numeric,2) AS fees
FROM paper_fills f
JOIN paper_positions p ON p.id=f.position_id
JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default';

SELECT p.symbol, round(p.realized_pnl::numeric,2) AS pos_pnl,
       round(coalesce(sum(f.pnl),0)::numeric,2) AS fill_pnl_sum,
       round(p.realized_pnl::numeric - coalesce(sum(f.pnl),0)::numeric, 4) AS delta
FROM paper_positions p
JOIN paper_accounts a ON a.id=p.account_id
LEFT JOIN paper_fills f ON f.position_id=p.id
WHERE a.name='default' AND p.status='closed'
GROUP BY p.id, p.symbol, p.realized_pnl
HAVING abs(p.realized_pnl::numeric - coalesce(sum(f.pnl),0)::numeric) > 0.02
ORDER BY abs(p.realized_pnl::numeric - coalesce(sum(f.pnl),0)::numeric) DESC
LIMIT 20;

-- Trendline skip detail
SELECT p.symbol, p.opened_at::text, left(p.notes,140) AS note
FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id
WHERE a.name='default'
  AND (p.notes ILIKE '%broke_falling%' OR p.notes ILIKE '%broke_rising%' OR p.notes ILIKE '%too_close_%')
ORDER BY p.opened_at;
SQL

echo "==> desk snapshot"
curl -fsS http://127.0.0.1:8000/api/v1/desk/snapshot -o /tmp/desk_audit.json
python3 <<'PY'
import json
d=json.load(open("/tmp/desk_audit.json"))
p=d.get("portfolio") or {}
trades=d.get("trades") or []
print("DESK equity", p.get("equity"), "realized", p.get("realizedPnl"),
      "closed", p.get("closedTrades"), "open", p.get("openPositions"),
      "pending", p.get("pendingOrders"), "trades_n", len(trades))
bad=[]
for t in trades:
    st=t.get("status")
    if st=="CLOSED" and t.get("exitPrice") is None and t.get("closedAt"):
        # some schemas use different keys
        pass
    if st=="CLOSED" and not (t.get("notes") or t.get("exitReason") or t.get("exit_reason")):
        bad.append(("no_exit_meta", t.get("symbol")))
print("desk_trade_statuses", {s: sum(1 for t in trades if t.get("status")==s) for s in sorted({t.get("status") for t in trades})})
for t in trades:
    print(t.get("symbol"), t.get("status"), t.get("realizedPnl") or t.get("pnl"), t.get("notes") or t.get("exitReason"))
if bad:
    print("BAD", bad)
PY

echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) post-TL paper audit done ====="
