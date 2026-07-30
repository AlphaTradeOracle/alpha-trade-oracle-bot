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
const PF_TIMELINE = __PF_TIMELINE__ as const;

const TRADE_HEADERS = __TRADE_HEADERS__;
const TRADE_ROWS = __TRADE_ROWS__ as const;
const CLOSED_ROWS = __CLOSED_ROWS__ as const;
const OPEN_ROWS = __OPEN_ROWS__ as const;
const PENDING_ROWS = __PENDING_ROWS__ as const;

const TIMELINE_HEADERS = __TIMELINE_HEADERS__;
const TIMELINE_ROWS = __TIMELINE_ROWS__ as const;

function money(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

function toneFor(n: number): "success" | "danger" | undefined {
  if (n > 0) return "success";
  if (n < 0) return "danger";
  return undefined;
}

export default function PaperTradesPerformance() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Paper Trades — Performance (MTF v2, ADX=20)</H1>
        <Text tone="secondary">
          Production VPS · since 2026-07-28 · Stand __GENERATED_INLINE__ · SIGNAL_MIN_ADX=20 (ADX≥35 rejected)
        </Text>
      </Stack>

      <Callout tone="warning">
        ADX ≥ 35 was tested live (15 closed, PF 0.41, −$133) — counterfactual filter sim did not include
        retest replay. Reverted to ADX=20; current ledger reflects MTF v2 weights only.
      </Callout>

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
        <Stat
          value={`${KPI.totalR >= 0 ? "+" : ""}${KPI.totalR.toFixed(2)}R`}
          label={`Total R (${KPI.rTrades} Trades mit 1R)`}
          tone={toneFor(KPI.totalR)}
        />
        <Stat
          value={`${KPI.expectancyR >= 0 ? "+" : ""}${KPI.expectancyR.toFixed(3)}R`}
          label="Erwartungswert je Trade"
          tone={toneFor(KPI.expectancyR)}
        />
        <Stat
          value={String(KPI.pfR)}
          label={`Profit Factor (R) · Ø Win ${KPI.avgWinR.toFixed(2)}R / Ø Loss ${KPI.avgLossR.toFixed(2)}R`}
          tone={KPI.pfR >= 1 ? "success" : "danger"}
        />
        <Stat
          value={`${KPI.feesR.toFixed(2)}R`}
          label={`Gebuehren · ${KPI.entries} Entries · max ${KPI.maxOpen} parallel`}
          tone="info"
        />
      </Grid>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat value={`$${KPI.cash.toFixed(2)}`} label="Cash Balance" />
        <Stat value={money(KPI.closedRpnl)} label="Closed RPnL Summe" tone={toneFor(KPI.closedRpnl)} />
        <Stat
          value={`${KPI.openN} / ${KPI.closedN} / ${KPI.pendingN}`}
          label="Open / Closed / Pending"
          tone="info"
        />
        <Stat value={String(KPI.cancelledN)} label="Cancelled (Retest skip)" tone="secondary" />
      </Grid>

      <Stack gap={8}>
        <H2>Timeline: Baseline → MTF v2 → ADX 35 (rejected) → Current</H2>
        <Table headers={TIMELINE_HEADERS} rows={TIMELINE_ROWS} striped framed />
        <Text tone="secondary" size="small">
          Phasenvergleich · ADX≥35 nur als abgelehntes Experiment · Quelle: VPS Postgres · {GENERATED}
        </Text>
      </Stack>

      <Grid columns={{ sm: 1, md: 2 }} gap={16}>
        <Stack gap={8}>
          <H2>Profit Factor Timeline</H2>
          <BarChart
            categories={PF_TIMELINE.map((d) => d.label)}
            series={[{ name: "Profit Factor", data: PF_TIMELINE.map((d) => d.value) }]}
            height={200}
          />
          <Text tone="secondary" size="small">
            PF by deployment phase · Baseline PF = 1.16 · Quelle: VPS · {GENERATED}
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Exit-Mix (Closed Trades)</H2>
          <BarChart
            categories={EXIT_COUNT_CHART.map((d) => d.label)}
            series={[{ name: "Trades", data: EXIT_COUNT_CHART.map((d) => d.value) }]}
            height={200}
          />
          <Text tone="secondary" size="small">
            Anzahl geschlossener Trades nach Exit-Grund · n={KPI.closedN} · {GENERATED}
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
          <Table headers={TRADE_HEADERS} rows={OPEN_ROWS} striped framed />
        </Stack>
      ) : null}

      {PENDING_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Pending Retest ({KPI.pendingN})</H2>
          <Table headers={TRADE_HEADERS} rows={PENDING_ROWS} striped framed />
        </Stack>
      ) : null}

      {CLOSED_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>All Closed Trades ({KPI.closedN})</H2>
          <Table headers={TRADE_HEADERS} rows={CLOSED_ROWS} striped framed />
        </Stack>
      ) : null}

      <Stack gap={8}>
        <H2>Complete Ledger ({TRADE_ROWS.length} positions)</H2>
        <Table headers={TRADE_HEADERS} rows={TRADE_ROWS} striped framed />
        <Text tone="secondary" size="small">
          Entry / SL / TP levels · Hit flags (TP1/TP2/TP3) · R-Multiple und 1R-Risiko je Trade ·
          Quelle: VPS Postgres · {GENERATED}
        </Text>
      </Stack>

      <Card>
        <CardHeader>Depot-Konfiguration</CardHeader>
        <CardBody>
          <Text size="small">
            Start ${KPI.start} · risikonormiertes Sizing (1R je Trade) · Leverage 10x ·
            MTF v2 weights · SIGNAL_MIN_ADX=20 · Cancelled {KPI.cancelledN} · Update {GENERATED}
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
