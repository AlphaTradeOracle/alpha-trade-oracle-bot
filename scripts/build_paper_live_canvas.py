"""Build paper-live-dashboard.canvas.tsx from VPS pipe-delimited export."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT = ROOT / "exports" / "paper_live_snapshot.txt"
DEFAULT_OUT = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot\canvases\paper-live-dashboard.canvas.tsx"
)


def parse_export(text: str) -> dict[str, object]:
    meta = "unknown"
    account: dict[str, float] = {}
    status_counts: dict[str, tuple[int, float]] = {}
    wr = {"wins": 0, "losses": 0, "wr": 0.0, "pf": 0.0}
    exits: list[dict[str, object]] = []
    top: list[list[str]] = []
    bot: list[list[str]] = []
    open_rows: list[list[str]] = []
    pend_rows: list[list[str]] = []

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
        elif tag == "TOP":
            top.append(parts[1:])
        elif tag == "BOT":
            bot.append(parts[1:])
        elif tag == "OPEN":
            open_rows.append(parts[1:])
        elif tag == "PEND":
            pend_rows.append(parts[1:])

    closed_n = status_counts.get("closed", (0, 0))[0]
    open_n = status_counts.get("open", (0, 0))[0]
    pending_n = status_counts.get("pending", (0, 0))[0]
    cancelled_n = status_counts.get("cancelled", (0, 0))[0]
    closed_rpnl = status_counts.get("closed", (0, 0.0))[1]

    start = account.get("start", 5000.0)
    realized = account.get("realized", 0.0)
    cash = account.get("cash", 0.0)
    book = start + realized
    open_upnl = sum(float(r[4]) for r in open_rows) if open_rows else 0.0
    equity = cash + open_upnl

    return {
        "generated": meta,
        "account": account,
        "kpi": {
            "start": start,
            "cash": cash,
            "book": book,
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
        "topClosed": top,
        "bottomClosed": bot,
        "open": open_rows,
        "pending": pend_rows,
    }


def render_canvas(data: dict[str, object]) -> str:
    kpi = data["kpi"]
    exits = data["exits"]
    generated = data["generated"]

    top_headers = ["ID", "Symbol", "Side", "RPnL $", "Exit", "Opened UTC", "Closed UTC"]
    top_rows = [
        [r[0], r[1], r[2], f"${float(r[3]):+.2f}", r[4], r[5], r[6]]
        for r in data["topClosed"]
    ]

    open_headers = ["ID", "Symbol", "Side", "Entry", "RPnL $", "Opened UTC"]
    open_rows = [
        [r[0], r[1], r[2], r[3], f"${float(r[4]):+.2f}", r[5]]
        for r in data["open"]
    ]

    pend_headers = ["ID", "Symbol", "Side", "Ref Entry", "Armed UTC"]
    pend_rows = [[r[0], r[1], r[2], r[3], r[4]] for r in data["pending"]]

    bot_headers = ["ID", "Symbol", "Side", "RPnL $", "Exit"]
    bot_rows = [[r[0], r[1], r[2], f"${float(r[3]):+.2f}", r[4]] for r in data["bottomClosed"]]

    exit_chart = [{"label": e["reason"], "value": e["n"]} for e in exits]
    exit_pnl = [{"label": e["reason"], "value": e["pnl"]} for e in exits]

    kpi_json = json.dumps(kpi, indent=2)
    exit_json = json.dumps(exits, indent=2)
    exit_chart_json = json.dumps(exit_chart, indent=2)
    exit_pnl_json = json.dumps(exit_pnl, indent=2)
    top_rows_json = json.dumps(top_rows, indent=2)
    open_rows_json = json.dumps(open_rows, indent=2)
    pend_rows_json = json.dumps(pend_rows, indent=2)
    bot_rows_json = json.dumps(bot_rows, indent=2)

    template = Path(__file__).with_name("_paper_live_canvas.template.tsx").read_text(encoding="utf-8")
    replacements = {
        "__GENERATED__": json.dumps(generated),
        "__KPI__": kpi_json,
        "__EXIT_MIX__": exit_json,
        "__EXIT_COUNT_CHART__": exit_chart_json,
        "__EXIT_PNL_CHART__": exit_pnl_json,
        "__TOP_HEADERS__": json.dumps(top_headers),
        "__TOP_ROWS__": top_rows_json,
        "__OPEN_HEADERS__": json.dumps(open_headers),
        "__OPEN_ROWS__": open_rows_json,
        "__PEND_HEADERS__": json.dumps(pend_headers),
        "__PEND_ROWS__": pend_rows_json,
        "__BOT_HEADERS__": json.dumps(bot_headers),
        "__BOT_ROWS__": bot_rows_json,
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
    print(f"Wrote {out_path} · generated={data['generated']}")


if __name__ == "__main__":
    main()
