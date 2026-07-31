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

const GENERATED = "2026-07-31 21:21 Europe/Berlin";
const KPI = {
  "start": 5000.0,
  "cash": 4547.75,
  "realized": 507.20334906,
  "closedRpnl": 120.1,
  "closedN": 58,
  "openN": 3,
  "pendingN": 3,
  "cancelledN": 44,
  "wr": 36.2,
  "wins": 21,
  "losses": 37,
  "pf": 1.14,
  "liveN": 6,
  "totalSnap": 109
} as const;
const EXIT_LABELS = ["stop_loss", "expired", "take_profit_3"] as const;
const EXIT_COUNTS = [37, 19, 2] as const;
const EXIT_PNLS = [-746.47, 535.97, 330.6] as const;

const LIVE_HEADERS = ["ID", "Symbol", "Side", "Status", "Entry", "SL", "TP2", "Score", "RPnL", "Opened"] as const;
const LIVE_ROWS = [
  [
    "1519",
    "KTAUSD",
    "SHORT",
    "pending",
    "0.104200",
    "0.107611",
    "0.097360",
    "23.2",
    "$0.00",
    "2026-07-31 16:32"
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
    "$0.00",
    "2026-07-31 17:00"
  ],
  [
    "1521",
    "ATOMUSDT",
    "SHORT",
    "open",
    "1.240825",
    "1.257221",
    "1.199834",
    "24.7",
    "$0.00",
    "2026-07-31 17:00"
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
    "$0.00",
    "2026-07-31 17:49"
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
    "$0.00",
    "2026-07-31 18:00"
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
    "$0.00",
    "2026-07-31 19:06"
  ]
] as const;

const CLOSED_HEADERS = ["ID", "Symbol", "Side", "RPnL", "Exit", "Opened", "Closed"] as const;
const CLOSED_ROWS = [
  [
    "1343",
    "KAIAUSDT",
    "SHORT",
    "$-26.07",
    "stop_loss",
    "2026-07-28 10:00",
    "2026-07-28 13:00"
  ],
  [
    "1342",
    "GRTUSDT",
    "SHORT",
    "$-18.23",
    "stop_loss",
    "2026-07-28 10:00",
    "2026-07-29 00:00"
  ],
  [
    "1345",
    "BATUSDT",
    "SHORT",
    "+$28.73",
    "expired",
    "2026-07-28 10:00",
    "2026-07-29 10:00"
  ],
  [
    "1344",
    "THETAUSDT",
    "SHORT",
    "+$35.29",
    "expired",
    "2026-07-28 11:00",
    "2026-07-29 10:00"
  ],
  [
    "1347",
    "MTLUSDT",
    "SHORT",
    "+$7.06",
    "expired",
    "2026-07-28 11:00",
    "2026-07-29 10:00"
  ],
  [
    "1349",
    "MLKUSDT",
    "SHORT",
    "$-8.78",
    "stop_loss",
    "2026-07-28 12:00",
    "2026-07-28 14:00"
  ],
  [
    "1353",
    "FETUSDT",
    "SHORT",
    "$-11.56",
    "expired",
    "2026-07-29 03:00",
    "2026-07-30 03:00"
  ],
  [
    "1371",
    "RENDERUSDT",
    "SHORT",
    "+$7.02",
    "expired",
    "2026-07-29 05:00",
    "2026-07-30 05:00"
  ],
  [
    "1359",
    "FETUSDT",
    "SHORT",
    "$-26.96",
    "stop_loss",
    "2026-07-29 05:00",
    "2026-07-29 18:00"
  ],
  [
    "1361",
    "TIAUSDT",
    "SHORT",
    "+$12.53",
    "stop_loss",
    "2026-07-29 05:00",
    "2026-07-29 22:00"
  ],
  [
    "1362",
    "SEIUSDT",
    "SHORT",
    "+$6.80",
    "expired",
    "2026-07-29 05:00",
    "2026-07-30 05:00"
  ],
  [
    "1363",
    "GRTUSDT",
    "SHORT",
    "$-6.32",
    "expired",
    "2026-07-29 05:00",
    "2026-07-30 05:00"
  ],
  [
    "1365",
    "AXSUSDT",
    "SHORT",
    "$-14.05",
    "stop_loss",
    "2026-07-29 05:00",
    "2026-07-29 19:00"
  ],
  [
    "1366",
    "THETAUSDT",
    "SHORT",
    "+$11.28",
    "stop_loss",
    "2026-07-29 05:00",
    "2026-07-30 01:00"
  ],
  [
    "1368",
    "BATUSDT",
    "SHORT",
    "+$21.98",
    "expired",
    "2026-07-29 05:00",
    "2026-07-30 05:00"
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
    "1364",
    "COMPUSDT",
    "SHORT",
    "$-13.76",
    "stop_loss",
    "2026-07-29 06:00",
    "2026-07-29 09:00"
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
    "1372",
    "FETUSDT",
    "SHORT",
    "$-26.04",
    "stop_loss",
    "2026-07-29 07:00",
    "2026-07-29 18:00"
  ],
  [
    "1375",
    "COMPUSDT",
    "SHORT",
    "$-12.91",
    "stop_loss",
    "2026-07-29 08:00",
    "2026-07-29 15:00"
  ],
  [
    "1370",
    "MEGAUSDT",
    "SHORT",
    "+$49.25",
    "expired",
    "2026-07-29 08:00",
    "2026-07-30 05:00"
  ],
  [
    "1376",
    "FETUSDT",
    "SHORT",
    "$-26.79",
    "stop_loss",
    "2026-07-29 10:00",
    "2026-07-29 11:00"
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
    "1377",
    "WEMIXUSDT",
    "SHORT",
    "$-42.77",
    "stop_loss",
    "2026-07-29 12:00",
    "2026-07-30 07:00"
  ],
  [
    "1381",
    "EIGENUSDT",
    "SHORT",
    "$-3.39",
    "expired",
    "2026-07-29 13:00",
    "2026-07-30 13:00"
  ],
  [
    "1383",
    "UNIUSDT",
    "LONG",
    "$-26.12",
    "stop_loss",
    "2026-07-29 13:00",
    "2026-07-29 19:00"
  ],
  [
    "1380",
    "TIAUSDT",
    "SHORT",
    "+$11.10",
    "stop_loss",
    "2026-07-29 13:00",
    "2026-07-29 22:00"
  ],
  [
    "1385",
    "MEGAUSDT",
    "SHORT",
    "+$18.25",
    "stop_loss",
    "2026-07-29 14:00",
    "2026-07-30 10:00"
  ],
  [
    "1387",
    "AKTUSDT",
    "SHORT",
    "$-25.02",
    "stop_loss",
    "2026-07-29 16:00",
    "2026-07-29 17:00"
  ],
  [
    "1389",
    "ARKMUSDT",
    "SHORT",
    "$-12.53",
    "stop_loss",
    "2026-07-29 16:00",
    "2026-07-29 18:00"
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
    "1392",
    "SEIUSDT",
    "SHORT",
    "$-23.74",
    "stop_loss",
    "2026-07-29 17:00",
    "2026-07-30 16:00"
  ],
  [
    "1399",
    "KSMUSDT",
    "SHORT",
    "$-14.41",
    "stop_loss",
    "2026-07-29 17:00",
    "2026-07-30 05:00"
  ],
  [
    "1386",
    "SEIUSDT",
    "SHORT",
    "$-21.55",
    "stop_loss",
    "2026-07-29 17:00",
    "2026-07-30 16:00"
  ],
  [
    "1396",
    "IMXUSDT",
    "SHORT",
    "+$2.50",
    "expired",
    "2026-07-29 17:00",
    "2026-07-30 17:00"
  ],
  [
    "1391",
    "TIAUSDT",
    "SHORT",
    "+$10.90",
    "stop_loss",
    "2026-07-29 17:00",
    "2026-07-29 22:00"
  ],
  [
    "1390",
    "MEGAUSDT",
    "SHORT",
    "+$5.31",
    "expired",
    "2026-07-29 18:00",
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
    "1400",
    "MEGAUSDT",
    "SHORT",
    "$-4.83",
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
    "1415",
    "MEGAUSDT",
    "SHORT",
    "$-31.27",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 01:00"
  ],
  [
    "1413",
    "IMXUSDT",
    "SHORT",
    "$-20.03",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 07:00"
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
    "1410",
    "SEIUSDT",
    "SHORT",
    "$-20.77",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 14:00"
  ],
  [
    "1411",
    "THETAUSDT",
    "SHORT",
    "$-20.07",
    "stop_loss",
    "2026-07-29 22:00",
    "2026-07-30 01:00"
  ],
  [
    "1423",
    "KSMUSDT",
    "SHORT",
    "$-23.33",
    "stop_loss",
    "2026-07-29 23:00",
    "2026-07-30 06:00"
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
    "1420",
    "SEIUSDT",
    "SHORT",
    "$-30.06",
    "stop_loss",
    "2026-07-29 23:00",
    "2026-07-30 16:00"
  ],
  [
    "1417",
    "KSMUSDT",
    "SHORT",
    "$-15.77",
    "stop_loss",
    "2026-07-29 23:00",
    "2026-07-30 05:00"
  ],
  [
    "1421",
    "GENIUSUSDT",
    "SHORT",
    "$-12.51",
    "expired",
    "2026-07-29 23:00",
    "2026-07-30 22:37"
  ],
  [
    "1426",
    "BATUSDT",
    "SHORT",
    "$-17.12",
    "stop_loss",
    "2026-07-30 03:00",
    "2026-07-30 07:00"
  ],
  [
    "1430",
    "BATUSDT",
    "SHORT",
    "$-18.34",
    "stop_loss",
    "2026-07-30 03:00",
    "2026-07-30 07:00"
  ],
  [
    "1437",
    "BATUSDT",
    "SHORT",
    "$-17.53",
    "stop_loss",
    "2026-07-30 04:00",
    "2026-07-30 06:00"
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
    "1440",
    "UAIUSDT",
    "LONG",
    "$-89.68",
    "stop_loss",
    "2026-07-30 05:00",
    "2026-07-30 06:00"
  ],
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
    "1444",
    "FLIPUSDT",
    "LONG",
    "$-6.83",
    "stop_loss",
    "2026-07-30 10:00",
    "2026-07-30 17:00"
  ]
] as const;

const CANCEL_HEADERS = ["ID", "Symbol", "Side", "Reason", "Opened", "Closed"] as const;
const CANCEL_ROWS = [
  [
    "1346",
    "SCUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-28 09:48",
    "2026-07-30 22:36"
  ],
  [
    "1341",
    "FETUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-28 09:48",
    "2026-07-30 22:36"
  ],
  [
    "1350",
    "PROSUSDT",
    "LONG",
    "retest_skipped",
    "2026-07-28 10:49",
    "2026-07-30 22:36"
  ],
  [
    "1348",
    "XAVAUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-28 10:49",
    "2026-07-30 22:36"
  ],
  [
    "1351",
    "XCNUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-28 13:00",
    "2026-07-30 22:36"
  ],
  [
    "1352",
    "SOONUSDT",
    "LONG",
    "retest_skipped",
    "2026-07-28 13:00",
    "2026-07-30 22:36"
  ],
  [
    "1355",
    "THETAUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 02:27",
    "2026-07-30 22:36"
  ],
  [
    "1354",
    "COMPUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 02:27",
    "2026-07-30 22:36"
  ],
  [
    "1360",
    "NIGHTUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 04:27",
    "2026-07-30 22:36"
  ],
  [
    "1356",
    "SNDKBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 04:27",
    "2026-07-30 22:36"
  ],
  [
    "1357",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 04:27",
    "2026-07-30 22:36"
  ],
  [
    "1358",
    "AXLUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 04:27",
    "2026-07-30 22:36"
  ],
  [
    "1378",
    "BATUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 11:27",
    "2026-07-30 22:37"
  ],
  [
    "1382",
    "UBUSDT",
    "LONG",
    "retest_skipped",
    "2026-07-29 12:27",
    "2026-07-30 22:37"
  ],
  [
    "1379",
    "WEMIXUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 12:27",
    "2026-07-30 22:37"
  ],
  [
    "1384",
    "WEMIXUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 13:27",
    "2026-07-30 22:37"
  ],
  [
    "1395",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 16:27",
    "2026-07-30 22:37"
  ],
  [
    "1393",
    "WALUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 16:27",
    "2026-07-30 22:37"
  ],
  [
    "1394",
    "SNDKBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 16:27",
    "2026-07-30 22:37"
  ],
  [
    "1397",
    "AKTUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 16:27",
    "2026-07-30 22:37"
  ],
  [
    "1401",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 18:27",
    "2026-07-30 22:37"
  ],
  [
    "1403",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 19:27",
    "2026-07-30 22:37"
  ],
  [
    "1404",
    "ZKUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 19:27",
    "2026-07-30 22:37"
  ],
  [
    "1405",
    "TOSHIUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1406",
    "FILUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1407",
    "GALAXUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1408",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1409",
    "CRCLBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1412",
    "BATUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1414",
    "ARKMUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 21:27",
    "2026-07-30 22:37"
  ],
  [
    "1422",
    "BATUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 22:27",
    "2026-07-30 22:37"
  ],
  [
    "1418",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 22:27",
    "2026-07-30 22:37"
  ],
  [
    "1419",
    "CRCLBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-29 22:27",
    "2026-07-30 22:37"
  ],
  [
    "1425",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 00:27",
    "2026-07-30 22:37"
  ],
  [
    "1427",
    "LPTUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 00:27",
    "2026-07-30 22:37"
  ],
  [
    "1428",
    "UNIUSDT",
    "LONG",
    "retest_skipped",
    "2026-07-30 00:27",
    "2026-07-30 22:37"
  ],
  [
    "1429",
    "CHZUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 01:27",
    "2026-07-30 22:37"
  ],
  [
    "1436",
    "CHZUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 03:27",
    "2026-07-30 22:37"
  ],
  [
    "1432",
    "RSCUSD",
    "SHORT",
    "retest_skipped",
    "2026-07-30 03:27",
    "2026-07-30 22:37"
  ],
  [
    "1433",
    "FLIPUSDT",
    "LONG",
    "retest_skipped",
    "2026-07-30 03:27",
    "2026-07-30 22:37"
  ],
  [
    "1434",
    "XAVAUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 03:27",
    "2026-07-30 22:37"
  ],
  [
    "1435",
    "MUBUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 03:27",
    "2026-07-30 22:37"
  ],
  [
    "1439",
    "XAVAUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 04:27",
    "2026-07-30 22:37"
  ],
  [
    "1443",
    "XAVAUSDT",
    "SHORT",
    "retest_skipped",
    "2026-07-30 06:27",
    "2026-07-30 22:37"
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

export default function AllPaperTrades() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>All Paper Trades</H1>
        <Text tone="secondary">
          Live ledger + closed/cancelled snapshot · {GENERATED}
        </Text>
      </Stack>

      <Callout tone="warning" title="Two data sources">
        Live DB currently has only {KPI.liveN} positions (open/pending) after ledger reset.
        Closed ({KPI.closedN}) and cancelled ({KPI.cancelledN}) rows come from the pre-reset snapshot.
      </Callout>

      <Grid columns={4} gap={12}>
        <Stat value={String(KPI.liveN)} label="Live now" tone="info" />
        <Stat value={String(KPI.closedN)} label="Closed (snapshot)" tone="info" />
        <Stat value={`${KPI.wr}%`} label={`WR (${KPI.wins}W/${KPI.losses}L)`} tone="info" />
        <Stat value={String(KPI.pf)} label="Profit Factor" tone={KPI.pf >= 1 ? "success" : "danger"} />
      </Grid>

      <Grid columns={4} gap={12}>
        <Stat value={money(KPI.cash)} label="Live Cash" tone={toneFor(KPI.cash - KPI.start)} />
        <Stat value={money(KPI.realized)} label="Snapshot Realized" tone={toneFor(KPI.realized)} />
        <Stat value={money(KPI.closedRpnl)} label="Closed RPnL sum" tone={toneFor(KPI.closedRpnl)} />
        <Stat value={String(KPI.cancelledN)} label="Cancelled" tone="warning" />
      </Grid>

      <Row gap={8} wrap>
        <Pill tone="info">{KPI.openN} open</Pill>
        <Pill tone="info">{KPI.pendingN} pending</Pill>
        <Pill tone="success">{KPI.closedN} closed</Pill>
        <Pill tone="warning">{KPI.cancelledN} cancelled</Pill>
      </Row>

      <Stack gap={8}>
        <H2>Exit Mix (closed)</H2>
        <BarChart
          categories={[...EXIT_LABELS]}
          series={[{ name: "Trades", data: [...EXIT_COUNTS] }]}
          height={180}
        />
        <Row gap={8} wrap>
          {EXIT_LABELS.map((label, i) => (
            <Pill key={label} tone={EXIT_PNLS[i] >= 0 ? "success" : "danger"}>
              {label}: {EXIT_COUNTS[i]}x · {money(EXIT_PNLS[i])}
            </Pill>
          ))}
        </Row>
      </Stack>

      <Stack gap={8}>
        <H2>Live Positions ({KPI.liveN})</H2>
        <Table headers={[...LIVE_HEADERS]} rows={[...LIVE_ROWS]} striped />
      </Stack>

      <Stack gap={8}>
        <H2>All Closed Trades ({KPI.closedN})</H2>
        <Table headers={[...CLOSED_HEADERS]} rows={[...CLOSED_ROWS]} striped />
      </Stack>

      <Stack gap={8}>
        <H2>Cancelled / Retest skipped ({KPI.cancelledN})</H2>
        <Table headers={[...CANCEL_HEADERS]} rows={[...CANCEL_ROWS]} striped />
      </Stack>

      <Card>
        <CardHeader title="Totals" />
        <CardBody>
          <Text size="small">
            Snapshot positions {KPI.totalSnap} · Live {KPI.liveN} · Closed RPnL {money(KPI.closedRpnl)} ·
            Snapshot realized {money(KPI.realized)} · Live cash {money(KPI.cash)} · {GENERATED}
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
