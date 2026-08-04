"""Dump desk snapshot KPI fields."""
from __future__ import annotations

import json
import urllib.request

for label, url in (
    ("local", "http://127.0.0.1:8000/api/v1/desk/snapshot"),
    ("pub", "https://alpha-trade-oracle.com/api/v1/desk/snapshot"),
    ("top", "https://alpha-trade-oracle.com/api/v1/desk/top-coins?limit=5"),
):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            body = r.read()
            print(label, "status", r.status, "bytes", len(body))
            d = json.loads(body)
    except Exception as exc:
        print(label, "FAIL", type(exc).__name__, exc)
        continue
    if label == "top":
        print("  coins", len(d.get("coins") or []), "src", d.get("source"), "at", d.get("generatedAt"))
        continue
    port = d.get("portfolio") or {}
    print("  generatedAt", d.get("generatedAt"))
    for k in (
        "startBalance",
        "equity",
        "cash",
        "realizedPnl",
        "unrealizedPnl",
        "totalReturnPct",
        "winRate",
        "openPositions",
        "pendingOrders",
        "closedTrades",
    ):
        if k in port:
            print(f"  {k}", port[k])
    print("  equity_points", len(d.get("equity") or []), "trades", len(d.get("trades") or []))
