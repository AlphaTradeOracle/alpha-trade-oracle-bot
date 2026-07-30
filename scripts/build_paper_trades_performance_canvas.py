"""Build paper-trades-performance.canvas.tsx from VPS pipe-delimited export."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT = ROOT / "exports" / "paper_trades_performance.txt"
DEFAULT_OUT = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot\canvases\paper-trades-performance.canvas.tsx"
)

TIMELINE = [
    {
        "phase": "Baseline (pre-MTF)",
        "date": "2026-07-28",
        "closed": 58,
        "wr": 37.9,
        "pf": 1.16,
        "rpnl": 132.6,
        "note": "Original ledger before MTF v2 rescoring",
    },
    {
        "phase": "MTF v2 weights",
        "date": "2026-07-30",
        "closed": 58,
        "wr": 37.9,
        "pf": 1.16,
        "rpnl": 132.6,
        "note": "Rescore + rebuild with c8460e6 indicator weights",
    },
    {
        "phase": "ADX ≥ 35 (rejected)",
        "date": "2026-07-30",
        "closed": 15,
        "wr": 40.0,
        "pf": 0.41,
        "rpnl": -133.5,
        "note": "Live rebuild failed — counterfactual sim only; reverted to ADX=20",
    },
]


def parse_export(text: str) -> dict[str, object]:
    meta = "unknown"
    account: dict[str, float] = {}
    status_counts: dict[str, tuple[int, float]] = {}
    wr = {"wins": 0, "losses": 0, "wr": 0.0, "pf": 0.0}
    exits: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|")
        tag = parts[0]
        if tag == "META":
            meta = parts[1]
        elif tag == "ACCOUNT":
            account = {
                "start": float(parts[1]),
                "cash": float(parts[2]),
                "realized": float(parts[3]),
                "margin": float(parts[4]),
                "leverage": float(parts[5]),
            }
        elif tag == "STATUS":
            status_counts[parts[1]] = (int(parts[2]), float(parts[3]))
        elif tag == "WR":
            wr = {
                "wins": int(parts[1]),
                "losses": int(parts[2]),
                "wr": float(parts[3]),
                "pf": float(parts[4]),
            }
        elif tag == "EXIT":
            exits.append({"reason": parts[1], "n": int(parts[2]), "pnl": float(parts[3])})
        elif tag == "TRADE":
            trades.append(
                {
                    "id": parts[1],
                    "symbol": parts[2],
                    "side": parts[3],
                    "status": parts[4],
                    "entry": parts[5],
                    "sl": parts[6],
                    "curSl": parts[7],
                    "tp1": parts[8],
                    "tp2": parts[9],
                    "tp3": parts[10],
                    "tp1Hit": parts[11] == "1",
                    "tp2Hit": parts[12] == "1",
                    "tp3Hit": parts[13] == "1",
                    "rpnl": float(parts[14]),
                    "exit": parts[15],
                    "opened": parts[16],
                    "closed": parts[17],
                }
            )

    closed_n = status_counts.get("closed", (0, 0))[0]
    open_n = status_counts.get("open", (0, 0))[0]
    pending_n = status_counts.get("pending", (0, 0))[0]
    cancelled_n = status_counts.get("cancelled", (0, 0))[0]
    closed_rpnl = status_counts.get("closed", (0, 0))[1]

    start = account.get("start", 5000.0)
    realized = account.get("realized", 0.0)
    cash = account.get("cash", 0.0)
    open_upnl = sum(t["rpnl"] for t in trades if t["status"] == "open")
    equity = cash + open_upnl

    return {
        "generated": meta,
        "account": account,
        "kpi": {
            "start": start,
            "cash": cash,
            "book": start + realized,
            "equity": equity,
            "realized": realized,
            "closedRpnl": closed_rpnl,
            "openN": open_n,
            "closedN": closed_n,
            "pendingN": pending_n,
            "cancelledN": cancelled_n,
            "wr": wr["wr"],
            "wins": wr["wins"],
            "losses": wr["losses"],
            "pf": wr["pf"],
        },
        "exits": exits,
        "trades": trades,
    }


def _hit_flags(t: dict[str, object]) -> str:
    flags = []
    if t["tp1Hit"]:
        flags.append("TP1")
    if t["tp2Hit"]:
        flags.append("TP2")
    if t["tp3Hit"]:
        flags.append("TP3")
    return ",".join(flags) if flags else "—"


def render_canvas(data: dict[str, object]) -> str:
    kpi = data["kpi"]
    exits = data["exits"]
    generated = data["generated"]
    trades = data["trades"]

    trade_headers = [
        "ID",
        "Symbol",
        "Side",
        "Status",
        "Entry",
        "SL",
        "TP1",
        "TP2",
        "TP3",
        "Hits",
        "RPnL",
        "Exit",
        "Opened",
        "Closed",
    ]
    trade_rows = [
        [
            t["id"],
            t["symbol"],
            t["side"].replace("STRONG_", ""),
            t["status"],
            t["entry"],
            t["sl"],
            t["tp1"],
            t["tp2"],
            t["tp3"],
            _hit_flags(t),
            f"${t['rpnl']:+.2f}",
            t["exit"] or "—",
            t["opened"],
            t["closed"] or "—",
        ]
        for t in trades
    ]

    closed_rows = [r for r in trade_rows if r[3] == "closed"]
    open_rows = [r for r in trade_rows if r[3] == "open"]
    pending_rows = [r for r in trade_rows if r[3] == "pending"]

    timeline_headers = ["Phase", "Date", "Closed", "WR", "PF", "Closed RPnL", "Note"]
    timeline_rows = [
        [
            p["phase"],
            p["date"],
            str(p["closed"]),
            f"{p['wr']}%",
            str(p["pf"]),
            f"${p['rpnl']:+.2f}",
            p["note"],
        ]
        for p in TIMELINE
    ]
    timeline_rows.append(
        [
            "Current (ADX=20, MTF v2)",
            generated.split()[0] if generated else "—",
            str(kpi["closedN"]),
            f"{kpi['wr']}%",
            str(kpi["pf"]),
            f"${kpi['closedRpnl']:+.2f}",
            "Live VPS ledger after revert + rebuild",
        ]
    )

    exit_chart = [{"label": e["reason"], "value": e["n"]} for e in exits]
    pf_timeline = [
        {"label": "Baseline", "value": 1.16},
        {"label": "MTF v2", "value": 1.16},
        {"label": "ADX35 rej.", "value": 0.41},
        {"label": "Current", "value": float(kpi["pf"])},
    ]

    template = Path(__file__).with_name("_paper_trades_performance.template.tsx").read_text(
        encoding="utf-8"
    )
    replacements = {
        "__GENERATED__": json.dumps(generated),
        "__KPI__": json.dumps(kpi, indent=2),
        "__EXIT_MIX__": json.dumps(exits, indent=2),
        "__EXIT_COUNT_CHART__": json.dumps(exit_chart, indent=2),
        "__PF_TIMELINE__": json.dumps(pf_timeline, indent=2),
        "__TRADE_HEADERS__": json.dumps(trade_headers),
        "__TRADE_ROWS__": json.dumps(trade_rows, indent=2),
        "__CLOSED_ROWS__": json.dumps(closed_rows, indent=2),
        "__OPEN_ROWS__": json.dumps(open_rows, indent=2),
        "__PENDING_ROWS__": json.dumps(pending_rows, indent=2),
        "__TIMELINE_HEADERS__": json.dumps(timeline_headers),
        "__TIMELINE_ROWS__": json.dumps(timeline_rows, indent=2),
        "__GENERATED_INLINE__": generated,
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


def main() -> None:
    export_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPORT
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT

    if not export_path.exists():
        print(f"Export file not found: {export_path}", file=sys.stderr)
        sys.exit(1)

    data = parse_export(export_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_canvas(data), encoding="utf-8")
    print(f"Wrote {out_path} · {data['kpi']['closedN']} closed · generated={data['generated']}")


if __name__ == "__main__":
    main()
