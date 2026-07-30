import {
  BarChart,
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
          Production VPS · paper_accounts + paper_positions · Stand __GENERATED_INLINE__ · Auto-Refresh alle 15 Min
        </Text>
      </Stack>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat
          value={money(KPI.equity)}
          label="Equity (Cash + Open RPnL)"
          tone={toneFor(KPI.equity - KPI.start)}
        />
        <Stat value={money(KPI.realized)} label="Account Realized PnL" tone={toneFor(KPI.realized)} />
        <Stat
          value={`${KPI.wr}%`}
          label={`Win Rate (${KPI.wins}W/${KPI.losses}L)`}
          tone="info"
        />
        <Stat value={String(KPI.pf)} label="Profit Factor" tone={KPI.pf >= 1 ? "success" : "danger"} />
      </Grid>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat value={`$${KPI.cash.toFixed(2)}`} label="Cash Balance" />
        <Stat value={`$${KPI.book.toFixed(2)}`} label="Book (Start + Realized)" />
        <Stat value={money(KPI.closedRpnl)} label="Closed RPnL Summe" tone={toneFor(KPI.closedRpnl)} />
        <Stat
          value={`${KPI.openN} / ${KPI.closedN} / ${KPI.pendingN}`}
          label="Open / Closed / Pending"
          tone="info"
        />
      </Grid>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Exit-Mix (Anzahl)</H2>
          <BarChart
            categories={EXIT_COUNT_CHART.map((d) => d.label)}
            series={[{ name: "Trades", data: EXIT_COUNT_CHART.map((d) => d.value) }]}
            height={200}
          />
          <Text tone="secondary" size="small">
            Anzahl geschlossener Trades nach Exit-Grund · n={KPI.closedN} · Quelle: VPS Postgres · {GENERATED}
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Exit-Mix (RPnL USD)</H2>
          <BarChart
            categories={EXIT_PNL_CHART.map((d) => d.label)}
            series={[{ name: "RPnL (USD)", data: EXIT_PNL_CHART.map((d) => d.value) }]}
            valuePrefix="$"
            beginAtZero={false}
            height={200}
          />
          <Text tone="secondary" size="small">
            Realisierter PnL je Exit-Grund · Quelle: VPS Postgres · {GENERATED}
          </Text>
        </Stack>
      </Grid>

      <Row gap={8} wrap>
        {EXIT_MIX.map((e) => (
          <Pill key={e.reason} tone={e.pnl >= 0 ? "success" : "deleted"}>
            {e.reason}: {e.n}× · {money(e.pnl)}
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
          <H2>Top Geschlossene</H2>
          <Table headers={TOP_HEADERS} rows={TOP_ROWS} striped framed />
        </Stack>
      ) : null}

      {BOT_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Schwächste Geschlossene</H2>
          <Table headers={BOT_HEADERS} rows={BOT_ROWS} striped framed />
        </Stack>
      ) : null}

      <Card>
        <CardHeader>Depot-Konfiguration</CardHeader>
        <CardBody>
          <Text size="small">
            Start ${KPI.start} · Margin $100 · Leverage 10x · Cancelled/Skipped {KPI.cancelledN} ·
            Letztes Update {GENERATED}
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
