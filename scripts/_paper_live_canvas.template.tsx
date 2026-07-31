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
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const GENERATED = __GENERATED__;
const KPI = __KPI__ as const;
const EXIT_MIX = __EXIT_MIX__ as const;
const EXIT_COUNT_CHART = __EXIT_COUNT_CHART__ as const;
const EXIT_PNL_CHART = __EXIT_PNL_CHART__ as const;
const STRATEGY = __STRATEGY__ as const;

const TOP_HEADERS = __TOP_HEADERS__;
const TOP_ROWS = __TOP_ROWS__ as const;

const OPEN_HEADERS = __OPEN_HEADERS__;
const OPEN_ROWS = __OPEN_ROWS__ as const;

const PEND_HEADERS = __PEND_HEADERS__;
const PEND_ROWS = __PEND_ROWS__ as const;

const BOT_HEADERS = __BOT_HEADERS__;
const BOT_ROWS = __BOT_ROWS__ as const;

function money(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

function toneFor(n: number): "success" | "danger" | undefined {
  if (n > 0) return "success";
  if (n < 0) return "danger";
  return undefined;
}

export default function PaperLiveDashboard() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Paper Trades — Live Dashboard</H1>
        <Text tone="secondary">
          Production VPS · Stand __GENERATED_INLINE__ · {STRATEGY.commit} · Regime + Short-Guard + Early Scratch
        </Text>
      </Stack>

      <Callout tone="info">
        Live: TP 2/4/6R · scale-out 50/25/25 · STRONG only · Long≥75 / Short 18–25 · RSI Short≥33 · Retest
        0.55×6 · min 1 Bar · Early Scratch 12h/0.5R · Portfolio 10%/10/6.
      </Callout>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat
          value={money(KPI.equity)}
          label="Equity (Cash + Open RPnL)"
          tone={toneFor(KPI.equity - KPI.start)}
        />
        <Stat value={money(KPI.realized)} label="Account Realized PnL" tone={toneFor(KPI.realized)} />
        <Stat
          value={`${KPI.totalR >= 0 ? "+" : ""}${KPI.totalR.toFixed(2)}R`}
          label={`Total R (${KPI.rTrades} Trades)`}
          tone={toneFor(KPI.totalR)}
        />
        <Stat
          value={`${KPI.expectancyR >= 0 ? "+" : ""}${KPI.expectancyR.toFixed(3)}R`}
          label="Expectancy / Trade"
          tone={toneFor(KPI.expectancyR)}
        />
      </Grid>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat
          value={`${KPI.wr}%`}
          label={`Win Rate (${KPI.wins}W/${KPI.losses}L)`}
          tone="info"
        />
        <Stat value={String(KPI.pfR)} label="Profit Factor (R)" tone={KPI.pfR >= 1 ? "success" : "danger"} />
        <Stat value={String(KPI.pf)} label="Profit Factor ($)" tone={KPI.pf >= 1 ? "success" : "danger"} />
        <Stat
          value={`${KPI.openN} / ${KPI.closedN} / ${KPI.pendingN}`}
          label="Open / Closed / Pending"
          tone="info"
        />
      </Grid>

      <Row gap={8} wrap>
        {STRATEGY.pills.map((p) => (
          <Pill key={p} tone="info">
            {p}
          </Pill>
        ))}
      </Row>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Exit-Mix (Anzahl)</H2>
          <BarChart
            categories={EXIT_COUNT_CHART.map((d) => d.label)}
            series={[{ name: "Trades", data: EXIT_COUNT_CHART.map((d) => d.value) }]}
            height={200}
          />
          <Text tone="secondary" size="small">
            Closed by exit reason · n={KPI.closedN} · VPS Postgres · {GENERATED}
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Exit-Mix (R)</H2>
          <BarChart
            categories={EXIT_PNL_CHART.map((d) => d.label)}
            series={[{ name: "R", data: EXIT_PNL_CHART.map((d) => d.value) }]}
            beginAtZero={false}
            height={200}
          />
          <Text tone="secondary" size="small">
            Sum R by exit reason · VPS Postgres · {GENERATED}
          </Text>
        </Stack>
      </Grid>

      <Row gap={8} wrap>
        {EXIT_MIX.map((e) => (
          <Pill key={e.reason} tone={e.r >= 0 ? "success" : "deleted"}>
            {e.reason}: {e.n}× · {e.r >= 0 ? "+" : ""}
            {e.r.toFixed(2)}R · {money(e.pnl)}
          </Pill>
        ))}
      </Row>

      {OPEN_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Open Positions ({KPI.openN})</H2>
          <Table headers={OPEN_HEADERS} rows={OPEN_ROWS} striped framed />
        </Stack>
      ) : null}

      {PEND_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Pending Retest ({KPI.pendingN})</H2>
          <Table headers={PEND_HEADERS} rows={PEND_ROWS} striped framed />
        </Stack>
      ) : null}

      {TOP_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Top Closed</H2>
          <Table headers={TOP_HEADERS} rows={TOP_ROWS} striped framed />
        </Stack>
      ) : null}

      {BOT_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Weakest Closed</H2>
          <Table headers={BOT_HEADERS} rows={BOT_ROWS} striped framed />
        </Stack>
      ) : null}

      <Card>
        <CardHeader>Depot</CardHeader>
        <CardBody>
          <Text size="small">
            Start ${KPI.start} · 1R=${STRATEGY.riskPerTradeUsd} · Fee {STRATEGY.feePercent}% · Leverage{" "}
            {STRATEGY.leverage}x · Cancelled {KPI.cancelledN} · {GENERATED}
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
