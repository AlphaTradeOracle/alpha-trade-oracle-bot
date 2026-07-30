"""Build paper-filters-sim.canvas.tsx from exports/paper_filters_sim.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = ROOT / "exports" / "paper_filters_sim.json"
DEFAULT_OUT = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot\canvases\paper-filters-sim.canvas.tsx"
)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _money(v: float) -> str:
    return f"${v:+.2f}"


def render_canvas(data: dict) -> str:
    baseline = data["baseline"]
    ranked = [r for r in data["ranked"] if r["key"] not in {"baseline_recorded", "baseline_mtf_v2"}]
    top = ranked[:12]
    sample = data["sample"]
    generated = data["generated_at"][:19].replace("T", " ")

    rows = []
    for r in top:
        rows.append(
            [
                r["key"],
                r["label"],
                str(r["n_taken"]),
                _money(float(r["total_pnl"])),
                _pct(float(r["win_rate"])),
                f"{float(r['profit_factor']):.2f}",
                _money(float(r.get("delta_pnl_vs_baseline") or 0)),
                f"{float(r.get('delta_pf_vs_baseline') or 0):+.2f}",
            ]
        )

    chart_cats = [r["label"][:28] for r in top[:8]]
    pf_series = [round(float(r["profit_factor"]), 3) for r in top[:8]]
    wr_series = [round(float(r["win_rate"]) * 100, 1) for r in top[:8]]

    return f'''import {{
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  Pill,
  Stack,
  Stat,
  Table,
  Text,
}} from "cursor/canvas";

const GENERATED = {json.dumps(generated)};
const BASELINE = {json.dumps({
    "n": baseline["n_taken"],
    "pnl": baseline["total_pnl"],
    "wr": baseline["win_rate"],
    "pf": baseline["profit_factor"],
    "dd": baseline["max_drawdown"],
})} as const;
const SAMPLE = {json.dumps({
    "closed": sample["n_closed"],
    "retest": sample["n_retest_fills"],
    "avgAdx": sample.get("avg_adx"),
    "avgScoreLong": sample.get("avg_score_long"),
    "avgScoreShort": sample.get("avg_score_short"),
})} as const;

const HEADERS = ["Key", "Variante", "n", "PnL", "WR", "PF", "ΔPnL", "ΔPF"];
const ROWS = {json.dumps(rows)} as const;

const PF_CHART = {json.dumps([{"label": c, "value": v} for c, v in zip(chart_cats, pf_series, strict=True)])} as const;
const WR_CHART = {json.dumps([{"label": c, "value": v} for c, v in zip(chart_cats, wr_series, strict=True)])} as const;

function tonePf(pf: number): "success" | "danger" | "info" {{
  if (pf >= BASELINE.pf) return "success";
  if (pf < 1) return "danger";
  return "info";
}}

export default function PaperFiltersSim() {{
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Paper Filter Simulation — MTF v2</H1>
        <Text tone="secondary">
          Counterfactual Gate-Sweep · {{SAMPLE.closed}} closed trades · Quelle VPS Postgres · {{GENERATED}} UTC
        </Text>
      </Stack>

      <Grid columns={{ sm: 2, md: 5 }} gap={12}>
        <Stat value={{String(BASELINE.n)}} label="Baseline Trades" tone="info" />
        <Stat value={{`$${{BASELINE.pnl.toFixed(2)}}`}} label="Baseline PnL" tone="success" />
        <Stat value={{`${{(BASELINE.wr * 100).toFixed(1)}}%`}} label="Baseline WR" tone="info" />
        <Stat value={{String(BASELINE.pf.toFixed(2))}} label="Baseline PF" tone="success" />
        <Stat value={{`$${{BASELINE.dd.toFixed(0)}}`}} label="Max Drawdown" tone="danger" />
      </Grid>

      <Callout tone="warning">
        Kleine Stichprobe (~{{SAMPLE.closed}} Trades), alle Retest-Fills. Strengere Score/R:R-Filter verschlechtern
        WR/PF in diesem Fenster — einziger klarer Gewinn: ADX ≥ 35 (+2.1pp WR, PF 1.30).
      </Callout>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Profit Factor (Top-8 Varianten)</H2>
          <BarChart
            categories={{PF_CHART.map((d) => d.label)}}
            series={{[{{ name: "Profit Factor", data: PF_CHART.map((d) => d.value) }}]}}
            height={{220}}
          />
          <Text tone="secondary" size="small">
            Baseline PF = {{BASELINE.pf.toFixed(2)}} · Höher = besser · n≥8 empfohlen
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Win Rate % (Top-8 Varianten)</H2>
          <BarChart
            categories={{WR_CHART.map((d) => d.label)}}
            series={{[{{ name: "Win Rate (%)", data: WR_CHART.map((d) => d.value) }}]}}
            height={{220}}
          />
          <Text tone="secondary" size="small">
            Baseline WR = {{(BASELINE.wr * 100).toFixed(1)}}% · Ø ADX {{SAMPLE.avgAdx}} · Ø Score L/S {{SAMPLE.avgScoreLong}}/{{SAMPLE.avgScoreShort}}
          </Text>
        </Stack>
      </Grid>

      <Card>
        <CardHeader title="Varianten-Ranking (nach PF)" trailing={{<Pill tone="info">vs Baseline</Pill>}} />
        <CardBody padding={{0}}>
          <Table headers={{HEADERS}} rows={{ROWS}} />
        </CardBody>
      </Card>
    </Stack>
  );
}}
'''


def main() -> None:
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT

    if not json_path.exists():
        print(f"JSON not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_canvas(data), encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
