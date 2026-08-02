#!/usr/bin/env python3
"""Build universe-300-vs-500.canvas.tsx from compare JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def usd(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "exports/universe_300_vs_500_30d.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    out = (
        Path.home()
        / ".cursor"
        / "projects"
        / "c-Users-Admin-Projects-alpha-trade-oracle-bot"
        / "canvases"
        / "universe-300-vs-500.canvas.tsx"
    )

    method = data["method"]
    buckets = data["buckets"]
    rec = data["recommendation"]
    generated = data["generated_at"].replace("T", " ")[:19]

    order = ["1-300", "301-500", "1-500"]
    if "universe-top" in buckets:
        order.append("universe-top")
    table_rows = []
    for name in order:
        b = buckets[name]
        table_rows.append(
            [
                name,
                str(b["symbols_ok"]),
                str(b["symbols_with_trades"]),
                str(b["total_trades"]),
                f"{b['avg_win_rate'] * 100:.1f}%",
                f"{b['avg_profit_factor']:.2f}",
                f"{b['expectancy_usd']:+.2f}",
                usd(b["total_net_profit"]),
                f"{b['profitable_symbol_pct']:.0f}%",
            ]
        )

    net_chart = [
        {"label": name, "value": round(float(buckets[name]["total_net_profit"]), 1)}
        for name in order
    ]
    exp_chart = [
        {"label": name, "value": round(float(buckets[name]["expectancy_usd"]), 2)}
        for name in order
    ]
    b_uni = buckets.get("universe-top")

    # Top/bottom incremental symbols in 301-500
    mid_syms = [
        s
        for s in data.get("symbols", [])
        if s.get("rank")
        and 301 <= int(s["rank"]) <= 500
        and "error" not in s
        and int(s.get("trades") or 0) > 0
    ]
    mid_syms_sorted = sorted(mid_syms, key=lambda s: float(s["net"]), reverse=True)
    best_mid = mid_syms_sorted[:8]
    worst_mid = list(reversed(mid_syms_sorted[-8:])) if len(mid_syms_sorted) >= 8 else list(
        reversed(mid_syms_sorted)
    )

    def sym_rows(items: list[dict]) -> list[list[str]]:
        return [
            [
                str(s["rank"]),
                s["symbol"],
                str(s["trades"]),
                f"{float(s['wr']) * 100:.0f}%",
                f"{float(s['pf']):.2f}" if s.get("pf") is not None else "n/a",
                usd(float(s["net"])),
            ]
            for s in items
        ]

    decision = rec["decision"]
    tone = "success" if decision == "consider_500" else "warning"
    reasons_js = json.dumps(rec["reasons"], ensure_ascii=False)
    table_js = json.dumps(table_rows, ensure_ascii=False)
    net_js = json.dumps(net_chart, ensure_ascii=False)
    exp_js = json.dumps(exp_chart, ensure_ascii=False)
    best_js = json.dumps(sym_rows(best_mid), ensure_ascii=False)
    worst_js = json.dumps(sym_rows(worst_mid), ensure_ascii=False)

    b300 = buckets["1-300"]
    bmid = buckets["301-500"]
    b500 = buckets["1-500"]

    content = f'''import {{
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

const GENERATED = {json.dumps(generated)};
const RANGE = {json.dumps(f"{method['start']} → {method['end']}")};
const DAYS = {int(method['days'])};
const TF = {json.dumps(method['timeframe'])};
const WORKERS = {int(method['workers'])};
const ELAPSED_MIN = {round(float(data['elapsed_seconds']) / 60, 1)};
const REQUESTED = {int(method['symbols_requested'])};
const WITH_CANDLES = {int(method['symbols_with_candles'])};

const NET_CHART = {net_js} as const;
const EXP_CHART = {exp_js} as const;
const HEADERS = ["Bucket", "OK", "With trades", "Trades", "WR", "PF", "Exp $/t", "Net", "Profitable %"] as const;
const ROWS = {table_js} as const;
const BEST_MID = {best_js} as const;
const WORST_MID = {worst_js} as const;
const SYM_HEADERS = ["Rank", "Symbol", "Trades", "WR", "PF", "Net"] as const;

const REC_HEADLINE = {json.dumps(rec['headline'])};
const REC_DECISION = {json.dumps(decision)};
const REASONS = {reasons_js} as const;

const NET_300 = {float(b300['total_net_profit'])};
const NET_MID = {float(bmid['total_net_profit'])};
const NET_500 = {float(b500['total_net_profit'])};
const NET_UNI = {float(b_uni['total_net_profit']) if b_uni else float('nan')};
const EXP_300 = {float(b300['expectancy_usd'])};
const EXP_MID = {float(bmid['expectancy_usd'])};
const PF_300 = {float(b300['avg_profit_factor'])};
const PF_MID = {float(bmid['avg_profit_factor'])};
const PF_UNI = {float(b_uni['avg_profit_factor']) if b_uni else float('nan')};
const TRADES_MID = {int(bmid['total_trades'])};
const HAS_UNI = {json.dumps(b_uni is not None)};

export default function Universe300Vs500() {{
  const tone = REC_DECISION === "consider_500" ? "success" : "warning";
  return (
    <Stack gap={{20}}>
      <Stack gap={{6}}>
        <H1>Universe 300 vs 500</H1>
        <Text tone="secondary">
          Baseline live gates · {{TF}} · {{DAYS}}d · {{RANGE}} · in_universe top-N by rank (fill-down) ·
          {{WORKERS}} workers · {{ELAPSED_MIN}} min · {{GENERATED}} UTC
        </Text>
      </Stack>

      <Grid columns={{{{ sm: 2, md: 4 }}}} gap={{12}}>
        <Stat value={{String(REQUESTED)}} label="Symbols loaded" />
        <Stat value={{String(WITH_CANDLES)}} label="With 1h candles" />
        <Stat value={{usd(NET_300)}} label="Net 1-300" tone={{NET_300 >= 0 ? "success" : "danger"}} />
        <Stat value={{usd(NET_MID)}} label="Net 301-500" tone={{NET_MID >= 0 ? "success" : "danger"}} />
      </Grid>
      {{HAS_UNI && Number.isFinite(NET_UNI) ? (
        <Grid columns={{{{ sm: 2, md: 2 }}}} gap={{12}}>
          <Stat value={{usd(NET_500)}} label="Net rank ≤500" tone={{NET_500 >= 0 ? "success" : "danger"}} />
          <Stat
            value={{usd(NET_UNI)}}
            label={{`Full universe-top (PF ${{PF_UNI.toFixed(2)}})`}}
            tone={{NET_UNI >= 0 ? "success" : "danger"}}
          />
        </Grid>
      ) : null}}

      <Callout tone={{tone}}>
        {{REC_HEADLINE}}. Incremental 301-500: {{usd(NET_MID)}} net · Exp {{EXP_MID >= 0 ? "+" : ""}}{{EXP_MID.toFixed(2)}}$/trade ·
        PF {{PF_MID.toFixed(2)}} · {{TRADES_MID}} trades. Core 1-300: {{usd(NET_300)}} · Exp {{EXP_300 >= 0 ? "+" : ""}}{{EXP_300.toFixed(2)}}$ ·
        PF {{PF_300.toFixed(2)}}. Totals are sums of independent $5k books (relative quality, not portfolio).
      </Callout>

      <Stack gap={{8}}>
        <H2>Why</H2>
        <Stack gap={{4}}>
          {{REASONS.map((r) => (
            <Text key={{r}}>• {{r}}</Text>
          ))}}
        </Stack>
      </Stack>

      <Grid columns={{{{ sm: 1, md: 2 }}}} gap={{16}}>
        <Stack gap={{8}}>
          <H2>Net profit by rank bucket ($)</H2>
          <BarChart
            categories={{NET_CHART.map((d) => d.label)}}
            series={{[{{ name: "Net profit ($)", data: NET_CHART.map((d) => d.value) }}]}}
            height={{220}}
          />
          <Text tone="secondary" size="small">
            Source: DB backtest baseline · sum of per-symbol $5k accounts
          </Text>
        </Stack>
        <Stack gap={{8}}>
          <H2>Expectancy $/trade by bucket</H2>
          <BarChart
            categories={{EXP_CHART.map((d) => d.label)}}
            series={{[{{ name: "Expectancy ($/trade)", data: EXP_CHART.map((d) => d.value) }}]}}
            height={{220}}
          />
          <Text tone="secondary" size="small">
            Source: same baseline run · net / trades
          </Text>
        </Stack>
      </Grid>

      <Card>
        <CardHeader>Bucket aggregates</CardHeader>
        <CardBody>
          <Table headers={{[...HEADERS]}} rows={{ROWS.map((r) => [...r])}} />
        </CardBody>
      </Card>

      <Grid columns={{{{ sm: 1, md: 2 }}}} gap={{16}}>
        <Card>
          <CardHeader>Best 301-500 symbols</CardHeader>
          <CardBody>
            <Table headers={{[...SYM_HEADERS]}} rows={{BEST_MID.map((r) => [...r])}} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Worst 301-500 symbols</CardHeader>
          <CardBody>
            <Table headers={{[...SYM_HEADERS]}} rows={{WORST_MID.map((r) => [...r])}} />
          </CardBody>
        </Card>
      </Grid>

      <Text tone="secondary" size="small">
        Rank buckets use market_cap_rank ranges. universe-top = all loaded in_universe symbols
        (ranks may exceed 500 via fill-down). Combined rank≤500 net {{usd(NET_500)}}.
        VPS now: UNIVERSE_TARGET_COUNT=500.
      </Text>
    </Stack>
  );
}}

function usd(v: number): string {{
  const sign = v >= 0 ? "+" : "-";
  return `${{sign}}$${{Math.abs(v).toLocaleString("en-US", {{ maximumFractionDigits: 0 }})}}`;
}}
'''
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
