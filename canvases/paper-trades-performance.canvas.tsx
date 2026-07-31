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

const GENERATED = "2026-07-31 21:19 Europe/Berlin";

const KPI = {
  "start": 5000.0,
  "cash": 4547.75,
  "realized": 507.20334906,
  "closedRpnl": 120.09,
  "closedN": 58,
  "openN": 3,
  "pendingN": 3,
  "cancelledN": 44,
  "wr": 36.2,
  "wins": 21,
  "losses": 37,
  "pf": 1.14,
  "longWr": 62.5,
  "longN": 8,
  "longPnl": 514.17,
  "shortWr": 32.0,
  "shortN": 50,
  "shortPnl": -394.07
} as const;

const EXIT_LABELS = ["stop_loss", "expired", "take_profit_3"] as const;
const EXIT_COUNTS = [37, 19, 2] as const;
const EXIT_PNLS = [-746.48, 535.96, 330.61] as const;

const PF_LABELS = ["Baseline", "MTF v2", "ADX35 rej.", "Risk+TP", "Current"] as const;
const PF_VALUES = [1.16, 1.16, 0.41, 1.5, 1.14] as const;

const LIVE_HEADERS = ["ID", "Symbol", "Side", "Status", "Entry", "SL", "TP2", "Score", "Opened"] as const;
const OPEN_ROWS = [
  [
    "1521",
    "ATOMUSDT",
    "SHORT",
    "open",
    "1.240825",
    "1.257221",
    "1.199834",
    "24.7",
    "07-31 17:00"
  ],
  [
    "1518",
    "IMXUSDT",
    "SHORT",
    "open",
    "0.106318",
    "0.108880",
    "0.099912",
    "18.1",
    "07-31 17:00"
  ],
  [
    "1520",
    "WUSDT",
    "SHORT",
    "open",
    "0.008025",
    "0.008166",
    "0.007670",
    "20.7",
    "07-31 18:00"
  ]
] as const;
const PENDING_ROWS = [
  [
    "1519",
    "KTAUSD",
    "SHORT",
    "pending",
    "0.104200",
    "0.107611",
    "0.097360",
    "23.2",
    "07-31 16:32"
  ],
  [
    "1522",
    "BATUSDT",
    "SHORT",
    "pending",
    "0.064300",
    "0.065035",
    "0.062830",
    "21.2",
    "07-31 17:49"
  ],
  [
    "1523",
    "KAVAUSDT",
    "SHORT",
    "pending",
    "0.041490",
    "0.041948",
    "0.040573",
    "23.2",
    "07-31 19:06"
  ]
] as const;

const CLOSED_HEADERS = ["ID", "Symbol", "Side", "RPnL", "Exit", "Opened", "Closed"] as const;
const WINNER_ROWS = [
  [
    "1441",
    "MMTUSDT",
    "LONG",
    "+$166.28",
    "take_profit_3",
    "2026-07-30 06:00",
    "2026-07-30 16:00"
  ],
  [
    "1438",
    "MMTUSDT",
    "LONG",
    "+$164.32",
    "take_profit_3",
    "2026-07-30 05:00",
    "2026-07-30 17:00"
  ],
  [
    "1388",
    "UNIUSDT",
    "LONG",
    "+$108.90",
    "expired",
    "2026-07-29 16:00",
    "2026-07-30 16:00"
  ],
  [
    "1398",
    "UNIUSDT",
    "LONG",
    "+$100.30",
    "expired",
    "2026-07-29 18:00",
    "2026-07-30 17:00"
  ],
  [
    "1402",
    "UNIUSDT",
    "LONG",
    "+$97.00",
    "expired",
    "2026-07-29 21:00",
    "2026-07-30 20:00"
  ],
  [
    "1367",
    "GENIUSUSDT",
    "SHORT",
    "+$54.17",
    "expired",
    "2026-07-29 06:00",
    "2026-07-30 05:00"
  ],
  [
    "1373",
    "GRASSUSDT",
    "SHORT",
    "+$50.27",
    "expired",
    "2026-07-29 07:00",
    "2026-07-30 07:00"
  ],
  [
    "1370",
    "MEGAUSDT",
    "SHORT",
    "+$49.25",
    "expired",
    "2026-07-29 08:00",
    "2026-07-30 05:00"
  ]
] as const;
const LOSER_ROWS = [
  [
    "1440",
    "UAIUSDT",
    "LONG",
    "$-89.68",
    "stop_loss",
    "2026-07-30 05:00",
    "2026-07-30 06:00"
  ],
  [
    "1369",
    "REUSDT",
    "SHORT",
    "$-53.71",
    "stop_loss",
    "2026-07-29 05:00",
    "2026-07-29 14:00"
  ],
  [
    "1377",
    "WEMIXUSDT",
    "SHORT",
    "$-42.77",
    "stop_loss",
    "2026-07-29 12:00",
    "2026-07-30 07:00"
  ],
  [
    "1374",
    "FETUSDT",
    "SHORT",
    "$-40.55",
    "stop_loss",
    "2026-07-29 11:00",
    "2026-07-30 08:00"
  ],
  [
    "1416",
    "FARTCOINUSDT",
    "SHORT",
    "$-34.26",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 13:00"
  ],
  [
    "1424",
    "MEGAUSDT",
    "SHORT",
    "$-31.48",
    "stop_loss",
    "2026-07-29 23:00",
    "2026-07-30 02:00"
  ],
  [
    "1415",
    "MEGAUSDT",
    "SHORT",
    "$-31.27",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 01:00"
  ],
  [
    "1420",
    "SEIUSDT",
    "SHORT",
    "$-30.06",
    "stop_loss",
    "2026-07-29 23:00",
    "2026-07-30 16:00"
  ]
] as const;

function money(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

function toneFor(n: number): "success" | "danger" | "info" {
  if (n > 0) return "success";
  if (n < 0) return "danger";
  return "info";
}

export default function PaperTradesPerformance() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Paper Trades Performance</H1>
        <Text tone="secondary">
          VPS since 2026-07-28 · Stand {GENERATED} · closed from snapshot · open/pending live
        </Text>
      </Stack>

      <Callout tone="warning" title="Live ledger reset">
        Live DB has {KPI.openN} open / {KPI.pendingN} pending / 0 closed. Closed stats below are from the
        last VPS snapshot before reset ({KPI.closedN} trades). Live cash {money(KPI.cash)}.
      </Callout>

      <Grid columns={4} gap={12}>
        <Stat value={money(KPI.cash)} label="Live Cash" tone={toneFor(KPI.cash - KPI.start)} />
        <Stat value={money(KPI.realized)} label="Snapshot Realized" tone={toneFor(KPI.realized)} />
        <Stat value={`${KPI.wr}%`} label={`Win Rate (${KPI.wins}W/${KPI.losses}L)`} tone="info" />
        <Stat value={String(KPI.pf)} label="Profit Factor" tone={KPI.pf >= 1 ? "success" : "danger"} />
      </Grid>

      <Grid columns={4} gap={12}>
        <Stat value={String(KPI.closedN)} label="Closed (snapshot)" tone="info" />
        <Stat value={money(KPI.closedRpnl)} label="Closed RPnL" tone={toneFor(KPI.closedRpnl)} />
        <Stat
          value={`${KPI.longWr}%`}
          label={`LONG (${KPI.longN} · ${money(KPI.longPnl)})`}
          tone={toneFor(KPI.longPnl)}
        />
        <Stat
          value={`${KPI.shortWr}%`}
          label={`SHORT (${KPI.shortN} · ${money(KPI.shortPnl)})`}
          tone={toneFor(KPI.shortPnl)}
        />
      </Grid>

      <Row gap={8} wrap>
        <Pill tone="info">{KPI.openN} open</Pill>
        <Pill tone="info">{KPI.pendingN} pending</Pill>
        <Pill tone="warning">{KPI.cancelledN} cancelled (snapshot)</Pill>
        <Pill tone={KPI.pf >= 1 ? "success" : "danger"}>PF {KPI.pf}</Pill>
      </Row>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H2>Profit Factor Timeline</H2>
          <BarChart
            categories={[...PF_LABELS]}
            series={[{ name: "PF", data: [...PF_VALUES] }]}
            height={200}
          />
        </Stack>
        <Stack gap={8}>
          <H2>Exit Mix</H2>
          <BarChart
            categories={[...EXIT_LABELS]}
            series={[{ name: "Trades", data: [...EXIT_COUNTS] }]}
            height={200}
          />
        </Stack>
      </Grid>

      <Row gap={8} wrap>
        {EXIT_LABELS.map((label, i) => (
          <Pill key={label} tone={EXIT_PNLS[i] >= 0 ? "success" : "danger"}>
            {label}: {EXIT_COUNTS[i]}x · {money(EXIT_PNLS[i])}
          </Pill>
        ))}
      </Row>

      {OPEN_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Open Positions ({KPI.openN})</H2>
          <Table headers={[...LIVE_HEADERS]} rows={[...OPEN_ROWS]} striped />
        </Stack>
      ) : null}

      {PENDING_ROWS.length > 0 ? (
        <Stack gap={8}>
          <H2>Pending Retest ({KPI.pendingN})</H2>
          <Table headers={[...LIVE_HEADERS]} rows={[...PENDING_ROWS]} striped />
        </Stack>
      ) : null}

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H2>Top Winners</H2>
          <Table headers={[...CLOSED_HEADERS]} rows={[...WINNER_ROWS]} striped />
        </Stack>
        <Stack gap={8}>
          <H2>Top Losers</H2>
          <Table headers={[...CLOSED_HEADERS]} rows={[...LOSER_ROWS]} striped />
        </Stack>
      </Grid>

      <Card>
        <CardHeader title="Notes" />
        <CardBody>
          <Text size="small">
            Snapshot: {KPI.closedN} closed · WR {KPI.wr}% · PF {KPI.pf} · Realized {money(KPI.realized)} ·
            Cancelled {KPI.cancelledN}. Live cash after reset: {money(KPI.cash)}. Generated {GENERATED}.
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
