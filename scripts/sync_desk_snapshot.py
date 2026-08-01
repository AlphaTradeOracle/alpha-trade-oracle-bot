"""Build dashboard trades/portfolio/equity JSON from a paper desk export."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.desk_service import map_raw_export_to_snapshot  # noqa: E402


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "exports" / "desk_raw.json"
    out_dir = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else ROOT.parent / "alpha-trade-oracle-dashboard" / "trading-dashboard" / "src" / "data"
    )
    raw_text = src.read_text(encoding="utf-8").strip()
    # psql may prepend status lines — keep the JSON object only.
    start = raw_text.find("{")
    if start < 0:
        raise SystemExit(f"No JSON object in {src}")
    payload = json.loads(raw_text[start:])
    snap = map_raw_export_to_snapshot(payload)

    out_dir.mkdir(parents=True, exist_ok=True)
    trades = [t.model_dump(mode="json") for t in snap.trades]
    portfolio = snap.portfolio.model_dump(mode="json")
    equity = [e.model_dump(mode="json") for e in snap.equity]

    (out_dir / "trades.json").write_text(json.dumps(trades, indent=2) + "\n", encoding="utf-8")
    (out_dir / "portfolio.json").write_text(
        json.dumps(portfolio, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "equity.json").write_text(json.dumps(equity, indent=2) + "\n", encoding="utf-8")

    closed = [t for t in snap.trades if t.status == "CLOSED"]
    zero = [t for t in closed if t.exit is None or (t.realized or 0) == 0 and t.exit is None]
    print(
        f"wrote {out_dir}: trades={len(snap.trades)} "
        f"open={portfolio['openPositions']} pending={portfolio['pendingOrders']} "
        f"closed={portfolio['closedTrades']} zero_exit_closed={len(zero)}"
    )


if __name__ == "__main__":
    main()
