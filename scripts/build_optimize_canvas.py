#!/usr/bin/env python3
"""Build strategy-optimize-top300.canvas.tsx from optimize JSON."""

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
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "exports/optimize_top300_30d.json")
    default_out = (
        Path.home()
        / ".cursor"
        / "projects"
        / "c-Users-Admin-Projects-alpha-trade-oracle-bot"
        / "canvases"
        / "strategy-optimize-top300.canvas.tsx"
    )
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else default_out

    data = json.loads(src.read_text(encoding="utf-8"))
    method = data["method"]
    baseline = data.get("baseline") or {}
    bsum = baseline.get("summary") or {}
    ranked = data.get("ranked") or []
    winners = data.get("winners_vs_baseline") or []

    by_group: dict[str, list] = {}
    for row in ranked:
        by_group.setdefault(row["group"], []).append(row)
    best_per_group = []
    for group, rows in by_group.items():
        if group == "baseline":
            continue
        best = max(rows, key=lambda r: float(r["summary"]["total_net_profit"]))
        best_per_group.append(best)
    best_per_group.sort(key=lambda r: float(r["delta_vs_baseline"] or -1e18), reverse=True)

    pnl_chart = [
        {"label": r["key"], "value": round(float(r["summary"]["total_net_profit"]), 1)}
        for r in ranked[:10]
    ]
    delta_chart = [
        {"label": r["key"], "value": round(float(r["delta_vs_baseline"] or 0), 1)}
        for r in ranked
        if r["key"] != "baseline"
    ][:8]

    headers = ["Rank", "Key", "Group", "Trades", "WR", "PF", "Net", "Δ vs Base"]
    table_rows = []
    for i, r in enumerate(ranked, start=1):
        s = r["summary"]
        table_rows.append(
            [
                str(i),
                r["key"],
                r["group"],
                str(int(s["total_trades"])),
                pct(float(s["avg_win_rate"])),
                f"{float(s['avg_profit_factor']):.2f}",
                usd(float(s["total_net_profit"])),
                usd(float(r["delta_vs_baseline"] or 0)),
            ]
        )

    recs = [
        f"{r['key']}: {r['label']} → {usd(float(r['delta_vs_baseline'] or 0))} vs baseline"
        for r in winners[:5]
    ]
    if not recs:
        recs = ["Keine Variante schlaegt die Baseline im Net-Profit."]

    generated = str(data.get("generated_at", ""))[:19].replace("T", " ")
    best = ranked[0] if ranked else None
    range_s = f"{method.get('start')} → {method.get('end')}"

    lines = [
        "import {",
        "  BarChart,",
        "  Callout,",
        "  Card,",
        "  CardBody,",
        "  CardHeader,",
        "  Grid,",
        "  H1,",
        "  H2,",
        "  Pill,",
        "  Stack,",
        "  Stat,",
        "  Table,",
        "  Text,",
        '} from "cursor/canvas";',
        "",
        f"const GENERATED = {json.dumps(generated)};",
        f"const TOP_N = {int(method.get('top_n') or 0)};",
        f"const DAYS = {int(method.get('days') or 0)};",
        f"const TF = {json.dumps(method.get('timeframe'))};",
        f"const RANGE = {json.dumps(range_s)};",
        f"const VARIANTS = {int(method.get('variants') or len(ranked))};",
        f"const WORKERS = {int(method.get('workers') or 0)};",
        f"const ELAPSED_MIN = {round(float(data.get('elapsed_seconds') or 0) / 60, 1)};",
        f"const BASE_NET = {float(bsum.get('total_net_profit') or 0)};",
        f"const BASE_TRADES = {int(bsum.get('total_trades') or 0)};",
        f"const BASE_WR = {float(bsum.get('avg_win_rate') or 0)};",
        f"const BASE_PF = {float(bsum.get('avg_profit_factor') or 0)};",
        f"const WINNERS = {len(winners)};",
    ]
    if best:
        lines += [
            f"const BEST_KEY = {json.dumps(best['key'])};",
            f"const BEST_LABEL = {json.dumps(best['label'])};",
            f"const BEST_NET = {float(best['summary']['total_net_profit'])};",
            f"const BEST_DELTA = {float(best.get('delta_vs_baseline') or 0)};",
            f"const BEST_TRADES = {int(best['summary']['total_trades'])};",
            f"const BEST_PF = {float(best['summary']['avg_profit_factor'])};",
        ]
    else:
        lines += [
            'const BEST_KEY = "";',
            'const BEST_LABEL = "";',
            "const BEST_NET = 0;",
            "const BEST_DELTA = 0;",
            "const BEST_TRADES = 0;",
            "const BEST_PF = 0;",
        ]

    lines += [
        f"const PNL_CHART = {json.dumps(pnl_chart)} as const;",
        f"const DELTA_CHART = {json.dumps(delta_chart)} as const;",
        f"const HEADERS = {json.dumps(headers)} as const;",
        f"const ROWS = {json.dumps(table_rows)} as const;",
        f"const RECS = {json.dumps(recs)} as const;",
        "const GROUP_BEST = "
        + json.dumps(
            [
                {
                    "group": r["group"],
                    "key": r["key"],
                    "label": r["label"],
                    "delta": float(r.get("delta_vs_baseline") or 0),
                    "net": float(r["summary"]["total_net_profit"]),
                }
                for r in best_per_group[:8]
            ]
        )
        + " as const;",
        "",
        "function usd(v: number): string {",
        '  const sign = v >= 0 ? "+" : "-";',
        '  return `${sign}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;',
        "}",
        "",
        "export default function StrategyOptimizeTop300() {",
        "  const beat = BEST_DELTA > 0;",
        "  return (",
        "    <Stack gap={20}>",
        "      <Stack gap={6}>",
        "        <H1>Strategy Optimize — Top {TOP_N} / {DAYS}d</H1>",
        '        <Text tone="secondary">',
        "          DB-Backtest {TF} · {RANGE} · {VARIANTS} Varianten · {WORKERS} workers · {ELAPSED_MIN} min · {GENERATED} UTC",
        "        </Text>",
        "      </Stack>",
        "",
        "      <Grid columns={{ sm: 2, md: 5 }} gap={12}>",
        '        <Stat value={usd(BASE_NET)} label="Baseline Net" tone={BASE_NET >= 0 ? "success" : "danger"} />',
        '        <Stat value={String(BASE_TRADES)} label="Baseline Trades" />',
        "        <Stat value={`${(BASE_WR * 100).toFixed(1)}%`} label=\"Baseline WR\" />",
        '        <Stat value={BASE_PF.toFixed(2)} label="Baseline PF" tone={BASE_PF >= 1 ? "success" : "danger"} />',
        '        <Stat value={String(WINNERS)} label="Varianten > Baseline" tone="info" />',
        "      </Grid>",
        "",
        '      <Callout tone={beat ? "success" : "warning"}>',
        "        {beat",
        "          ? `Bester Lauf: ${BEST_KEY} (${BEST_LABEL}) mit ${usd(BEST_NET)} Net / Δ ${usd(BEST_DELTA)} vs Baseline · ${BEST_TRADES} Trades · PF ${BEST_PF.toFixed(2)}.`",
        "          : `Keine Variante schlägt die Baseline im Net-Profit. Beste: ${BEST_KEY} (${usd(BEST_NET)}).`}{\" \"}",
        "        Screening der Top-{TOP_N} aus dem Top-300-Universe · Summe unabhängiger $5k-Konten (Ranking, kein Portfolio-Sim).",
        "      </Callout>",
        "",
        "      <Grid columns={{ sm: 1, md: 2 }} gap={16}>",
        "        <Stack gap={8}>",
        "          <H2>Net Profit — Top 10</H2>",
        "          <BarChart",
        "            categories={PNL_CHART.map((d) => d.label)}",
        '            series={[{ name: "Net Profit ($)", data: PNL_CHART.map((d) => d.value) }]}',
        "            height={240}",
        "          />",
        "        </Stack>",
        "        <Stack gap={8}>",
        "          <H2>Δ vs Baseline</H2>",
        "          <BarChart",
        "            categories={DELTA_CHART.map((d) => d.label)}",
        '            series={[{ name: "Delta ($)", data: DELTA_CHART.map((d) => d.value) }]}',
        "            height={240}",
        "          />",
        "        </Stack>",
        "      </Grid>",
        "",
        "      <Card>",
        "        <CardHeader>Empfehlungen</CardHeader>",
        "        <CardBody>",
        "          <Stack gap={8}>",
        "            {RECS.map((line) => (",
        "              <Text key={line}>{line}</Text>",
        "            ))}",
        "          </Stack>",
        "        </CardBody>",
        "      </Card>",
        "",
        "      <Stack gap={8}>",
        "        <H2>Bester je Gruppe</H2>",
        "        <Grid columns={{ sm: 2, md: 4 }} gap={10}>",
        "          {GROUP_BEST.map((g) => (",
        "            <Card key={g.key}>",
        '              <CardHeader trailing={<Pill tone={g.delta >= 0 ? "success" : "danger"}>{usd(g.delta)}</Pill>}>',
        "                {g.group}",
        "              </CardHeader>",
        "              <CardBody>",
        '                <Text weight="semibold">{g.key}</Text>',
        '                <Text tone="secondary" size="small">{g.label}</Text>',
        "                <Text size=\"small\">Net {usd(g.net)}</Text>",
        "              </CardBody>",
        "            </Card>",
        "          ))}",
        "        </Grid>",
        "      </Stack>",
        "",
        "      <Stack gap={8}>",
        "        <H2>Vollständiges Ranking</H2>",
        "        <Table headers={[...HEADERS]} rows={ROWS.map((r) => [...r])} />",
        '        <Text tone="secondary" size="small">',
        "          Quelle: market_candles · single-TF 1h · fee/slip paper-nah · generated {GENERATED}",
        "        </Text>",
        "      </Stack>",
        "    </Stack>",
        "  );",
        "}",
        "",
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
