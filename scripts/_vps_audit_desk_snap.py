#!/usr/bin/env python3
"""Pull desk snapshot fields for audit report."""
from __future__ import annotations

import json
import urllib.request

url = "http://127.0.0.1:8000/api/v1/desk/snapshot"
with urllib.request.urlopen(url, timeout=60) as resp:
    d = json.load(resp)

p = d.get("portfolio") or {}
print("=== PORTFOLIO ===")
for k in (
    "equity",
    "cashBalance",
    "realizedPnl",
    "unrealizedPnl",
    "winRatePct",
    "openR",
    "closedTrades",
    "openPositions",
    "pendingOrders",
    "marginLocked",
    "equityChangePct",
    "realizedChangePct",
):
    print(f"{k}: {p.get(k)}")

eq = d.get("equity") or []
print("=== EQUITY CURVE ===")
print(f"points: {len(eq)}")
if eq:
    print(f"first: {eq[0]}")
    print(f"last: {eq[-1]}")

print("=== TRADES ===")
for t in d.get("trades") or []:
    print(
        f"{t.get('symbol')} {t.get('status')} side={t.get('side')} "
        f"entry={t.get('entry')} stop={t.get('stop')} "
        f"margin={t.get('margin')} notional={t.get('notional')} "
        f"size={t.get('positionSize')} realized={t.get('realized')} "
        f"upnl={t.get('upnl')} r={t.get('r')} score={t.get('score')}"
    )

mr = d.get("marketRegime")
print("=== MARKET REGIME ===")
if mr:
    print(json.dumps(mr, indent=2)[:1200])
else:
    print("null")
