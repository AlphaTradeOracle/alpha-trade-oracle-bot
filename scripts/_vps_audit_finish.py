#!/usr/bin/env python3
"""Parse audit outputs + DB stats for hundred audit."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def load_json_blob(path: str) -> dict:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    i = raw.find("{")
    if i < 0:
        return {"_error": "no_json", "_tail": raw[-1500:]}
    try:
        return json.loads(raw[i:])
    except json.JSONDecodeError:
        d, _ = json.JSONDecoder().raw_decode(raw[i:])
        return d


def main() -> None:
    out: dict = {}

    pv = load_json_blob("/tmp/paper_verify_full.json")
    v = pv.get("verdict") or {}
    a = pv.get("account") or {}
    b = pv.get("db_book") or {}
    out["paper_verify"] = {
        "FINAL_OK": v.get("FINAL_OK"),
        "identity": a.get("identity_ok"),
        "cash": a.get("cash"),
        "realized": a.get("realized"),
        "expected_fills": v.get("expected_fills"),
        "db_closed": b.get("closed"),
        "db_open": b.get("open"),
        "db_pending": b.get("pending"),
        "db_cancelled": b.get("cancelled"),
        "missing_fills": v.get("missing_fills"),
        "extra_fills": v.get("extra_fills"),
        "geometry_mismatches": v.get("geometry_mismatches"),
        "should_have_traded": v.get("should_have_traded"),
        "live_pending_outside_allowlist": len(pv.get("live_pending_outside_allowlist") or []),
        "allowlist_pending_geometry": pv.get("allowlist_pending_geometry"),
    }

    dm_path = Path("/tmp/desk_math_audit.json")
    if dm_path.exists():
        dm = json.loads(dm_path.read_text(encoding="utf-8"))
        out["desk_math"] = {
            "final_ok": dm.get("final_ok"),
            "issues": dm.get("issues"),
            "warnings": dm.get("warnings"),
            "trade_ok": dm.get("trade_ok"),
            "trade_fail": dm.get("trade_fail"),
            "pending_n": dm.get("pending_n"),
            "open_n": dm.get("open_n"),
            "closed_n": dm.get("closed_n"),
            "pending_checks": dm.get("pending_checks"),
            "portfolio": dm.get("portfolio"),
        }
    else:
        out["desk_math"] = {
            "missing": True,
            "out": Path("/tmp/desk_math_run.out").read_text(errors="replace")[-2000:]
            if Path("/tmp/desk_math_run.out").exists()
            else "",
            "err": Path("/tmp/desk_math_run.err").read_text(errors="replace")[-1500:]
            if Path("/tmp/desk_math_run.err").exists()
            else "",
        }

    fs = load_json_blob("/tmp/full_system.out") if Path("/tmp/full_system.out").exists() else {}
    if fs and "_error" not in fs:
        out["full_system"] = {
            "findings": fs.get("findings"),
            "warnings": fs.get("warnings"),
            "config": fs.get("config"),
            "account": fs.get("account"),
            "summary": fs.get("summary") or fs.get("verdict"),
            "stale_pending": fs.get("stale_pending"),
            "universe": fs.get("universe"),
        }
    else:
        out["full_system"] = fs or {"missing": True}

    pe = Path("/tmp/perp_exits.out").read_text(errors="replace") if Path("/tmp/perp_exits.out").exists() else ""
    out["perp_exits"] = {
        "ALL_CHECKS_PASSED": "ALL_CHECKS_PASSED" in pe,
        "FINAL_OK": "FINAL_OK=True" in pe,
        "issues_line": next((ln for ln in pe.splitlines() if ln.startswith("issues=")), None),
        "venue_line": next((ln for ln in pe.splitlines() if ln.startswith("venue_ok=")), None),
        "replay_line": next((ln for ln in pe.splitlines() if ln.startswith("replay_sequence_ok=")), None),
    }

    for label, path in (("local", "/tmp/desk_local.json"), ("public", "/tmp/desk_pub.json")):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        p = d.get("portfolio") or {}
        trades = d.get("trades") or []
        out[f"desk_{label}"] = {
            "equity": p.get("equity"),
            "cash": p.get("cash"),
            "realized": p.get("realizedPnl") or p.get("accountRealizedPnl"),
            "open": p.get("openPositions"),
            "pending": p.get("pendingOrders"),
            "closed": p.get("closedTrades"),
            "winRate": p.get("winRatePct"),
            "totalReturn": p.get("totalReturnPct"),
            "trades_n": len(trades) if isinstance(trades, list) else None,
            "regime": bool(d.get("marketRegime") or d.get("market_regime")),
        }

    # DB via docker
    env = Path("/opt/alpha-trade-oracle-bot/.env").read_text(encoding="utf-8", errors="replace")
    pw = next(line.split("=", 1)[1] for line in env.splitlines() if line.startswith("POSTGRES_PASSWORD="))
    user = next(
        (line.split("=", 1)[1] for line in env.splitlines() if line.startswith("POSTGRES_USER=")),
        "alpha_trade_oracle",
    )
    db = next(
        (line.split("=", 1)[1] for line in env.splitlines() if line.startswith("POSTGRES_DB=")),
        "alpha_trade_oracle",
    )
    sql = """
SELECT 'paper|' || status || '|' || COUNT(*) || '|' || COALESCE(ROUND(SUM(realized_pnl)::numeric,2),0)
FROM paper_positions WHERE account_id=1 GROUP BY status ORDER BY status;
SELECT 'acct|' || ROUND(cash_balance::numeric,2) || '|' || ROUND(realized_pnl::numeric,2)
FROM paper_accounts WHERE id=1;
SELECT 'sig24|' || COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours';
SELECT 'act24|' || COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours' AND is_actionable = true;
SELECT 'disp24|' || COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '24 hours' AND dispatched_at IS NOT NULL;
SELECT 'uni|' || COUNT(*) FILTER (WHERE in_universe AND is_active) FROM assets;
"""
    r = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={pw}",
            "postgres",
            "psql",
            "-U",
            user,
            "-d",
            db,
            "-t",
            "-A",
            "-c",
            sql,
        ],
        cwd="/opt/alpha-trade-oracle-bot",
        capture_output=True,
        text=True,
    )
    out["db"] = {"stdout": r.stdout.strip(), "stderr": r.stderr.strip(), "code": r.returncode}

    jobs_sql = """
SELECT job_key || '|' || is_enabled || '|' || (interval_seconds/60) || '|' || COALESCE(last_status,'') || '|' ||
COALESCE(ROUND(EXTRACT(EPOCH FROM (NOW()-last_success_at))/60.0,1)::text,'') || '|' || LEFT(COALESCE(last_error,''),60)
FROM scheduled_jobs ORDER BY is_enabled DESC, job_key;
"""
    r2 = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            f"PGPASSWORD={pw}",
            "postgres",
            "psql",
            "-U",
            user,
            "-d",
            db,
            "-t",
            "-A",
            "-c",
            jobs_sql,
        ],
        cwd="/opt/alpha-trade-oracle-bot",
        capture_output=True,
        text=True,
    )
    out["jobs"] = r2.stdout.strip().splitlines()

    # pending trade details from desk
    d = json.loads(Path("/tmp/desk_local.json").read_text(encoding="utf-8"))
    pend = [t for t in (d.get("trades") or []) if str(t.get("status") or "").lower() == "pending"]
    out["pending_trades"] = [
        {
            "symbol": t.get("symbol"),
            "side": t.get("side") or t.get("direction"),
            "score": t.get("score"),
            "entry": t.get("entry"),
            "zone": [t.get("entryZoneLow"), t.get("entryZoneHigh")],
            "stop": t.get("stop") or t.get("stopLoss"),
            "tp1": t.get("tp1") or t.get("takeProfit1"),
            "openedAt": t.get("openedAt"),
        }
        for t in pend
    ]

    Path("/tmp/hundred_audit_summary.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
