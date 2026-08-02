#!/usr/bin/env python3
"""Build tp-ladder-top400.canvas.tsx from optimize JSON (TP-focused sweep)."""

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
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "exports/tp_ladder_top400_30d.json")
    out = Path(
        sys.argv[2]
        if len(sys.argv) > 2
        else Path.home()
        / ".cursor"
        / "projects"
        / "c-Users-Admin-Projects-alpha-trade-oracle-bot"
        / "canvases"
        / "tp-ladder-top400.canvas.tsx"
    )

    data = json.loads(src.read_text(encoding="utf-8"))
    method = data["method"]
    baseline = data.get("baseline") or {}
    bsum = baseline.get("summary") or {}
    ranked = data.get("ranked") or []
    winners = data.get("winners_vs_baseline") or []

    ladder_keys = {
        "baseline",
        "tp_123",
        "tp_tight",
        "tp_246",
        "tp_wide",
        "tp_358",
        "tp_4812",
        "tp_246_equal",
        "tp_wide_no_be",
        "tp_wide_exp48",
    }
    combo_keys = {
        "combo_adx30_wide",
        "combo_adx30_wide_rr",
        "combo_adx25_246_exp48",
        "combo_score78_wide",
        "combo_adx30_4812",
    }

    by_key = {r["key"]: r for r in ranked}
    ladder = [by_key[k] for k in [
        "tp_123", "tp_tight", "baseline", "tp_246", "tp_wide", "tp_358", "tp_4812",
        "tp_246_equal", "tp_wide_no_be", "tp_wide_exp48",
    ] if k in by_key]
    # baseline may equal tp_tight — keep unique order from ranked for ladder chart
    ladder_chart = [
        {"label": r["key"], "value": round(float(r["summary"]["total_net_profit"]), 1)}
        for r in ladder
    ]
    combo_rows = [by_key[k] for k in [
        "combo_adx30_wide",
        "combo_adx30_wide_rr",
        "combo_adx25_246_exp48",
        "combo_score78_wide",
        "combo_adx30_4812",
    ] if k in by_key]
    combo_chart = [
        {"label": r["key"], "value": round(float(r["summary"]["total_net_profit"]), 1)}
        for r in combo_rows
    ]

    delta_chart = [
        {"label": r["key"], "value": round(float(r["delta_vs_baseline"] or 0), 1)}
        for r in ranked
        if r["key"] != "baseline"
    ]

    headers = ["#", "Key", "Label", "Trades", "WR", "PF", "Net", "Δ vs Base", "DD%"]
    table_rows = []
    for i, r in enumerate(ranked, start=1):
        s = r["summary"]
        table_rows.append(
            [
                str(i),
                r["key"],
                r.get("label") or r["key"],
                str(int(s["total_trades"])),
                pct(float(s["avg_win_rate"])),
                f"{float(s['avg_profit_factor']):.2f}",
                usd(float(s["total_net_profit"])),
                usd(float(r["delta_vs_baseline"] or 0)),
                f"{float(s.get('worst_max_drawdown_percent') or s.get('max_drawdown_percent') or 0):.1f}",
            ]
        )

    best = ranked[0] if ranked else None
    best_ladder = max(ladder, key=lambda r: float(r["summary"]["total_net_profit"])) if ladder else None
    best_combo = max(combo_rows, key=lambda r: float(r["summary"]["total_net_profit"])) if combo_rows else None

    generated = str(data.get("generated_at", ""))[:19].replace("T", " ")
    range_s = f"{method.get('start')} → {method.get('end')}"
    elapsed = round(float(data.get("elapsed_seconds") or 0) / 60, 1)

    # Recommendation text
    if best and float(best.get("delta_vs_baseline") or 0) > 0:
        rec = (
            f"Bester Lauf: {best['key']} ({best.get('label')}) mit "
            f"{usd(float(best['summary']['total_net_profit']))} "
            f"({usd(float(best['delta_vs_baseline'] or 0))} vs Baseline)."
        )
        tone = "positive"
    elif best:
        rec = (
            f"Keine Variante schlägt die Baseline im Netto. "
            f"Nächster Kandidat: {best['key']} "
            f"({usd(float(best['summary']['total_net_profit']))})."
        )
        tone = "warning"
    else:
        rec = "Keine Ergebnisse."
        tone = "warning"

    higher_help = False
    live = by_key.get("baseline") or by_key.get("tp_tight")
    if live:
        live_net = float(live["summary"]["total_net_profit"])
        for key in ("tp_246", "tp_wide", "tp_358", "tp_4812"):
            row = by_key.get(key)
            if row and float(row["summary"]["total_net_profit"]) > live_net:
                higher_help = True
                break

    insight = (
        "Höhere TP-Leiter verbessert den Netto gegenüber Live 1.5/2.5/4R."
        if higher_help
        else "Höhere TP-Leiter allein verbessert den Netto gegenüber Live nicht — "
        "eher engere Exits oder Gate-Combos prüfen."
    )

    def j(obj: object) -> str:
        return json.dumps(obj, ensure_ascii=False)

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
        f"const GENERATED = {j(generated)};",
        f"const RANGE = {j(range_s)};",
        f"const TOP_N = {int(method.get('top_n') or 400)};",
        f"const DAYS = {int(method.get('days') or 30)};",
        f"const TF = {j(method.get('timeframe') or '1h')};",
        f"const ELAPSED_MIN = {elapsed};",
        f"const BASE_NET = {float(bsum.get('total_net_profit') or 0)};",
        f"const BASE_TRADES = {int(bsum.get('total_trades') or 0)};",
        f"const BASE_WR = {float(bsum.get('avg_win_rate') or 0)};",
        f"const BASE_PF = {float(bsum.get('avg_profit_factor') or 0)};",
        f"const WINNERS = {len(winners)};",
        f"const LADDER_CHART = {j(ladder_chart)};",
        f"const COMBO_CHART = {j(combo_chart)};",
        f"const DELTA_CHART = {j(delta_chart)};",
        f"const TABLE_ROWS = {j(table_rows)};",
        f"const HEADERS = {j(headers)};",
        f"const REC = {j(rec)};",
        f"const INSIGHT = {j(insight)};",
        f"const REC_TONE = {j(tone)};",
    ]
    if best:
        lines += [
            f"const BEST_KEY = {j(best['key'])};",
            f"const BEST_NET = {float(best['summary']['total_net_profit'])};",
            f"const BEST_DELTA = {float(best.get('delta_vs_baseline') or 0)};",
        ]
    if best_ladder:
        lines += [
            f"const BEST_LADDER = {j(best_ladder['key'])};",
            f"const BEST_LADDER_NET = {float(best_ladder['summary']['total_net_profit'])};",
        ]
    if best_combo:
        lines += [
            f"const BEST_COMBO = {j(best_combo['key'])};",
            f"const BEST_COMBO_NET = {float(best_combo['summary']['total_net_profit'])};",
        ]

    lines += [
        "",
        "export default function TpLadderTop400() {",
        "  return (",
        "    <Stack gap={24}>",
        "      <Stack gap={8}>",
        '        <H1>TP-Leiter Top-400 · 30 Tage</H1>',
        "        <Text tone=\"secondary\">",
        "          Live-Baseline vs. höhere TP1/TP2/TP3 und angepasste Combos. ",
        "          Scale-out default 50/25/25 · {TF} · {RANGE}",
        "        </Text>",
        "        <Text tone=\"tertiary\" size=\"small\">",
        "          Quelle: optimize_strategy_top300 · Top {TOP_N} in_universe · ",
        "          {DAYS}d · generiert {GENERATED} UTC · {ELAPSED_MIN} min",
        "        </Text>",
        "      </Stack>",
        "",
        "      <Callout tone={REC_TONE} title=\"Empfehlung\">",
        "        {REC} {INSIGHT}",
        "      </Callout>",
        "",
        "      <Grid columns={4} gap={16}>",
        '        <Stat label="Baseline Net" value={`$${BASE_NET.toFixed(0)}`} />',
        '        <Stat label="Baseline Trades" value={String(BASE_TRADES)} />',
        '        <Stat label="Baseline WR" value={`${(BASE_WR * 100).toFixed(1)}%`} />',
        '        <Stat label="Varianten > Baseline" value={String(WINNERS)} />',
        "      </Grid>",
        "",
    ]

    if best:
        lines += [
            "      <Grid columns={3} gap={16}>",
            "        <Stat",
            '          label="Bester Gesamtlauf"',
            "          value={BEST_KEY}",
            "          tone={BEST_DELTA > 0 ? \"positive\" : \"warning\"}",
            "        />",
            "        <Stat",
            '          label="Best Net"',
            "          value={`$${BEST_NET.toFixed(0)}`}",
            "          tone={BEST_NET >= 0 ? \"positive\" : \"danger\"}",
            "        />",
            "        <Stat",
            '          label="Δ vs Baseline"',
            "          value={`$${BEST_DELTA.toFixed(0)}`}",
            "          tone={BEST_DELTA > 0 ? \"positive\" : \"danger\"}",
            "        />",
            "      </Grid>",
            "",
        ]

    lines += [
        "      <Card>",
        '        <CardHeader>Netto-PnL nach TP-Leiter</CardHeader>',
        "        <CardBody>",
        "          <BarChart",
        "            data={LADDER_CHART}",
        '            xKey="label"',
        '            yKey="value"',
        '            xLabel="Variante"',
        '            yLabel="Net profit (USD)"',
        "          />",
        '          <Text tone="tertiary" size="small">',
        "            Live = 1.5/2.5/4R · höhere Leitern rechts. Scale 50/25/25 außer tp_246_equal.",
        "          </Text>",
        "        </CardBody>",
        "      </Card>",
        "",
    ]

    if combo_chart:
        lines += [
            "      <Card>",
            '        <CardHeader>Angepasste Strategie-Combos</CardHeader>',
            "        <CardBody>",
            "          <BarChart",
            "            data={COMBO_CHART}",
            '            xKey="label"',
            '            yKey="value"',
            '            xLabel="Combo"',
            '            yLabel="Net profit (USD)"',
            "          />",
            "        </CardBody>",
            "      </Card>",
            "",
        ]

    lines += [
        "      <Card>",
        '        <CardHeader>Δ Netto vs Baseline</CardHeader>',
        "        <CardBody>",
        "          <BarChart",
        "            data={DELTA_CHART}",
        '            xKey="label"',
        '            yKey="value"',
        '            xLabel="Variante"',
        '            yLabel="Delta USD vs baseline"',
        "          />",
        "        </CardBody>",
        "      </Card>",
        "",
        "      <Card>",
        '        <CardHeader>Alle Varianten (sortiert nach Netto)</CardHeader>',
        "        <CardBody>",
        "          <Table headers={HEADERS} rows={TABLE_ROWS} />",
        "        </CardBody>",
        "      </Card>",
        "",
        "      <Stack gap={6}>",
        '        <H2>Lesen</H2>',
        '        <Text tone="secondary">',
        "          Höhere TPs erhöhen den theoretischen Gewinn pro Treffer, senken aber die",
        "          Hit-Rate — besonders bei Scale-out 50% auf TP1. Combos mit ADX/Score",
        "          filtern Setups, bei denen weite Ziele realistischer sind.",
        "        </Text>",
        "      </Stack>",
        "    </Stack>",
        "  );",
        "}",
        "",
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
