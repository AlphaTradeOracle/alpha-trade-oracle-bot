import {
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
} from "cursor/canvas";

const GENERATED = "2026-07-30 22:08:25";
const BASELINE = {"n": 58, "pnl": 132.6, "wr": 0.3793, "pf": 1.1571, "dd": 429.24} as const;
const SAMPLE = {"closed": 58, "retest": 58, "avgAdx": 35.42, "avgScoreLong": 82.61, "avgScoreShort": 18.05} as const;

const HEADERS = ["Key", "Variante", "n", "PnL", "WR", "PF", "ΔPnL", "ΔPF"];
const ROWS = [["adx_35", "ADX \u2265 35", "30", "$+115.26", "40.0%", "1.30", "$-17.34", "+0.14"], ["dq_80", "Data quality \u2265 80", "50", "$+126.92", "36.0%", "1.18", "$-5.68", "+0.02"], ["score_75", "Score long\u226575 / short\u226425.0", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["score_78", "Score long\u226578 / short\u226422.0", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["score_80", "Score long\u226580 / short\u226420.0", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["retest_on", "Retest enabled (actual mix)", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["rr_2_5", "Min R:R \u2265 2.5", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["dq_70", "Data quality \u2265 70", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["combo_score_75_retest_on", "Score long\u226575 / short\u226425.0 + Retest enabled (actual mix)", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["combo_score_75_rr_2_5", "Score long\u226575 / short\u226425.0 + Min R:R \u2265 2.5", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["combo_score_75_dq_70", "Score long\u226575 / short\u226425.0 + Data quality \u2265 70", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"], ["combo_retest_on_rr_2_5", "Retest enabled (actual mix) + Min R:R \u2265 2.5", "58", "$+132.60", "37.9%", "1.16", "$+0.00", "+0.00"]] as const;

const PF_CHART = [{"label": "ADX \u2265 35", "value": 1.302}, {"label": "Data quality \u2265 80", "value": 1.176}, {"label": "Score long\u226575 / short\u226425.0", "value": 1.157}, {"label": "Score long\u226578 / short\u226422.0", "value": 1.157}, {"label": "Score long\u226580 / short\u226420.0", "value": 1.157}, {"label": "Retest enabled (actual mix)", "value": 1.157}, {"label": "Min R:R \u2265 2.5", "value": 1.157}, {"label": "Data quality \u2265 70", "value": 1.157}] as const;
const WR_CHART = [{"label": "ADX \u2265 35", "value": 40.0}, {"label": "Data quality \u2265 80", "value": 36.0}, {"label": "Score long\u226575 / short\u226425.0", "value": 37.9}, {"label": "Score long\u226578 / short\u226422.0", "value": 37.9}, {"label": "Score long\u226580 / short\u226420.0", "value": 37.9}, {"label": "Retest enabled (actual mix)", "value": 37.9}, {"label": "Min R:R \u2265 2.5", "value": 37.9}, {"label": "Data quality \u2265 70", "value": 37.9}] as const;

function tonePf(pf: number): "success" | "danger" | "info" {
  if (pf >= BASELINE.pf) return "success";
  if (pf < 1) return "danger";
  return "info";
}

export default function PaperFiltersSim() {
  return (
    <Stack gap=20>
      <Stack gap=6>
        <H1>Paper Filter Simulation — MTF v2</H1>
        <Text tone="secondary">
          Counterfactual Gate-Sweep · {SAMPLE.closed} closed trades · Quelle VPS Postgres · {GENERATED} UTC
        </Text>
      </Stack>

      <Grid columns={ sm: 2, md: 5 } gap=12>
        <Stat value={String(BASELINE.n)} label="Baseline Trades" tone="info" />
        <Stat value={`$${BASELINE.pnl.toFixed(2)}`} label="Baseline PnL" tone="success" />
        <Stat value={`${(BASELINE.wr * 100).toFixed(1)}%`} label="Baseline WR" tone="info" />
        <Stat value={String(BASELINE.pf.toFixed(2))} label="Baseline PF" tone="success" />
        <Stat value={`$${BASELINE.dd.toFixed(0)}`} label="Max Drawdown" tone="danger" />
      </Grid>

      <Callout tone="warning">
        Kleine Stichprobe (~{SAMPLE.closed} Trades), alle Retest-Fills. Strengere Score/R:R-Filter verschlechtern
        WR/PF in diesem Fenster — einziger klarer Gewinn: ADX ≥ 35 (+2.1pp WR, PF 1.30).
      </Callout>

      <Grid columns={ sm: 1, md: 2 } gap=16>
        <Stack gap=8>
          <H2>Profit Factor (Top-8 Varianten)</H2>
          <BarChart
            categories={PF_CHART.map((d) => d.label)}
            series={[{ name: "Profit Factor", data: PF_CHART.map((d) => d.value) }]}
            height={220}
          />
          <Text tone="secondary" size="small">
            Baseline PF = {BASELINE.pf.toFixed(2)} · Höher = besser · n≥8 empfohlen
          </Text>
        </Stack>
        <Stack gap=8>
          <H2>Win Rate % (Top-8 Varianten)</H2>
          <BarChart
            categories={WR_CHART.map((d) => d.label)}
            series={[{ name: "Win Rate (%)", data: WR_CHART.map((d) => d.value) }]}
            height={220}
          />
          <Text tone="secondary" size="small">
            Baseline WR = {(BASELINE.wr * 100).toFixed(1)}% · Ø ADX {SAMPLE.avgAdx} · Ø Score L/S {SAMPLE.avgScoreLong}/{SAMPLE.avgScoreShort}
          </Text>
        </Stack>
      </Grid>

      <Card>
        <CardHeader title="Varianten-Ranking (nach PF)" trailing={<Pill tone="info">vs Baseline</Pill>} />
        <CardBody padding={0}>
          <Table headers={HEADERS} rows={ROWS} />
        </CardBody>
      </Card>
    </Stack>
  );
}
