#!/usr/bin/env python3
"""Build profit-stack-sim.canvas.tsx from optimize JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def usd(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "exports/profit_stack_top100_14d.json")
    out = Path(
        sys.argv[2]
        if len(sys.argv) > 2
        else Path.home()
        / ".cursor"
        / "projects"
        / "c-Users-Admin-Projects-alpha-trade-oracle-bot"
        / "canvases"
        / "profit-stack-sim.canvas.tsx"
    )
    data = json.loads(src.read_text(encoding="utf-8"))
    method = data["method"]
    ranked = data.get("ranked") or []
    baseline = next((r for r in ranked if r["key"] in ("A_base", "baseline")), None)
    base_net = float((baseline or {}).get("summary", {}).get("total_net_profit") or 0)

    order = ["A_base", "B_tp1", "C_tp_scratch", "D_short_gate", "E_retest_deep", "F_stack"]
    by_key = {r["key"]: r for r in ranked}
    rows = [by_key[k] for k in order if k in by_key]
    if not rows:
        rows = ranked

    chart = [
        {"label": r["key"], "value": round(float(r["summary"]["total_net_profit"]), 1)}
        for r in rows
    ]
    best = max(rows, key=lambda r: float(r["summary"]["total_net_profit"]))
    best_sum = best["summary"]

    table_rows = []
    for i, r in enumerate(sorted(rows, key=lambda x: -float(x["summary"]["total_net_profit"])), 1):
        s = r["summary"]
        net = float(s["total_net_profit"])
        delta = net - base_net
        table_rows.append(
            [
                str(i),
                r["key"],
                r["label"],
                str(int(s["total_trades"])),
                pct(float(s["avg_win_rate"])),
                f"{float(s.get('avg_profit_factor') or s.get('portfolio_profit_factor') or 0):.2f}",
                usd(net),
                usd(delta),
            ]
        )

    chart_js = json.dumps(chart)
    rows_js = json.dumps(table_rows)
    gen = data.get("generated_at", "")[:19].replace("T", " ")

    tsx = f'''import {{
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  Stack,
  Stat,
  Table,
  Text,
}} from "cursor/canvas";

const GENERATED = {json.dumps(gen)};
const RANGE = {json.dumps(f"{method.get('start', '')} → {method.get('end', '')}")};
const TOP_N = {int(method.get("top_n") or 100)};
const DAYS = {int(method.get("days") or 14)};
const TF = {json.dumps(method.get("timeframe") or "1h")};
const BASE_NET = {base_net};
const BEST_KEY = {json.dumps(best["key"])};
const BEST_LABEL = {json.dumps(best["label"])};
const BEST_NET = {float(best_sum["total_net_profit"])};
const BEST_DELTA = {float(best_sum["total_net_profit"]) - base_net};
const BEST_TRADES = {int(best_sum["total_trades"])};
const BEST_WR = {float(best_sum["avg_win_rate"])};
const BEST_PF = {float(best_sum.get("avg_profit_factor") or 0)};
const NET_CHART = {chart_js} as const;
const HEADERS = ["Rank", "Key", "Label", "Trades", "WR", "PF", "Net", "Δ vs A_base"] as const;
const ROWS = {rows_js} as const;

function usd(v: number): string {{
  const sign = v >= 0 ? "+" : "-";
  return `${{sign}}$${{Math.abs(v).toLocaleString("en-US", {{ maximumFractionDigits: 0 }})}}`;
}}

export default function ProfitStackSim() {{
  const win = BEST_DELTA > 0;
  return (
    <Stack gap={{20}} style={{{{ padding: 20, maxWidth: 1100 }}}}>
      <Stack gap={{6}}>
        <H1>Profit Stack Sim — Top {{TOP_N}} / {{DAYS}}d</H1>
        <Text tone="secondary" size="small">
          Paper-autopsy levers · {{TF}} · {{RANGE}} · slippage 0 · {{GENERATED}} UTC
        </Text>
      </Stack>

      <Grid columns={{4}} gap={{12}}>
        <Stat value={{usd(BASE_NET)}} label="A_base Net" tone={{BASE_NET >= 0 ? "success" : "danger"}} />
        <Stat value={{usd(BEST_NET)}} label={{`Best: ${{BEST_KEY}}`}} tone={{BEST_NET >= 0 ? "success" : "danger"}} />
        <Stat value={{usd(BEST_DELTA)}} label="Δ vs base" tone={{win ? "success" : "warning"}} />
        <Stat value={{String(BEST_TRADES)}} label="Best trades" />
      </Grid>

      <Callout tone={{win ? "success" : "warning"}} title={{BEST_LABEL}}>
        Best stack {{BEST_KEY}}: {{usd(BEST_NET)}} ({{usd(BEST_DELTA)}} vs A_base) · WR {{(BEST_WR * 100).toFixed(1)}}% ·
        PF {{BEST_PF.toFixed(2)}} · {{BEST_TRADES}} trades. Goal was E[R]&gt;0 / fewer full stops — check Δ and trade count.
      </Callout>

      <Card>
        <CardHeader>Net PnL by variant</CardHeader>
        <CardBody>
          <BarChart
            categories={{NET_CHART.map((d) => d.label)}}
            series={{[{{ name: "Net $", data: NET_CHART.map((d) => d.value) }}]}}
            height={{240}}
          />
        </CardBody>
      </Card>

      <H2>Ranking</H2>
      <Table headers={{[...HEADERS]}} rows={{ROWS.map((r) => [...r])}} />

      <Callout tone="info" title="Variant legend">
        A_base = live defaults · B_tp1 = 1.0/2.0/3.5R · C_tp_scratch = 0.8/1.5/3R · D_short_gate =
        short≤22 + ADX≥35 · E_retest_deep = zone_near 0.75 + 2 bars · F_stack = B+D+E
      </Callout>

      <Text size="small" tone="tertiary">
        Source: {{src.as_posix()}} · optimize_strategy_top300.py profit group
      </Text>
    </Stack>
  );
}}
'''
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tsx, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
