import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  LineChart,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  useHostTheme,
} from "cursor/canvas";

/* Quelle: exports/lowvol_factor.json · 366d 4h-Panel long_current · 2025-07-31–2026-07-31 */

const GENERATED = "2026-07-31 · scripts/analyze_lowvol_factor.py";
const PANEL = "731 Scans · 366 Tage · 291 Assets · Gebühr 0,05%/Seite";

const IC_BY_HORIZON = [-4.4, -5.1, -7.3, -7.9, -8.4];

const DECILES = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10"];
const DECILE_MEAN_24H = [0.020, -0.043, -0.099, -0.085, -0.101, -0.113, -0.078, 0.047, 0.027, 0.410];
const DECILE_MEDIAN_24H = [-0.163, -0.261, -0.331, -0.376, -0.439, -0.505, -0.559, -0.561, -0.708, -1.018];

const MONTHS = ["08", "09", "10", "11", "12", "01", "02", "03", "04", "05", "06", "07"];
const MONTHLY_IC = [-9.4, -4.8, -7.0, -13.5, -6.9, -10.9, -4.8, -5.1, -4.6, -6.0, -6.6, -6.4];

const BACKTEST = [
  ["Low-Vol Top-20", "+0,002 R", "[−0,068, +0,072]", "7.744"],
  ["High-Vol Top-20", "+0,029 R", "[−0,022, +0,080]", "7.794"],
  ["Mean Rev. RSI", "+0,017 R", "[−0,050, +0,088]", "9.417"],
  ["Mean Rev. 72h", "+0,008 R", "[−0,056, +0,068]", "9.234"],
  ["Low-Vol + Reversal", "−0,004 R", "[−0,079, +0,070]", "9.781"],
  ["Score Top-20", "+0,010 R", "[−0,040, +0,061]", "8.337"],
  ["Zufall Top-20", "−0,031 R", "[−0,096, +0,036]", "13.401"],
];

const BREAK_EVEN = [
  ["4h", "−0,028 R", "nein"],
  ["8h", "−0,014 R", "nein"],
  ["24h", "+0,002 R", "nein"],
  ["72h", "−0,002 R", "nein"],
  ["120h", "−0,014 R", "nein"],
];

const COMBO = [
  ["nur Low-Vol", "+0,073", "+0,05 %", "—"],
  ["nur RSI (Reversal)", "+0,057", "+0,22 %", "—"],
  ["nur 72h-Reversal", "+0,057", "+0,22 %", "ja"],
  ["Low-Vol + RSI", "+0,076", "−0,05 %", "—"],
  ["Low-Vol + Reversal", "+0,069", "−0,04 %", "—"],
];

export default function LowVolFactorCanvas() {
  const theme = useHostTheme();

  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <Row gap={8} align="center">
          <H1>Low-Vol-Faktor · atr_percent</H1>
          <Pill tone="warning">does not clear cost hurdle</Pill>
        </Row>
        <Text tone="secondary">
          {PANEL} · {GENERATED}
        </Text>
      </Stack>

      <Callout tone="danger" title="Nicht handelbar nach Gebühren">
        Der Faktor ist statistisch robust (IC −0,073, 13/13 Monate gleiches Vorzeichen), aber der
        Mittelwert-Vorteil fehlt: High-Vol-Dezil D10 hat +0,41 % Mean vs. D1 +0,02 %. Nach 0,05 %
        je Seite und Bar-Replay bleibt net R ≈ 0 mit CI über Null.
      </Callout>

      <Grid columns={4} gap={12}>
        <Stat label="IC 24h (atr_percent)" value="−0,073" tone="info" />
        <Stat label="Monate gleiches VZ." value="13 / 13" tone="success" />
        <Stat label="Median D1−D10 Spread" value="+0,85 bp" tone="success" />
        <Stat label="Mean D1−D10 Spread" value="−0,39 bp" tone="danger" />
      </Grid>

      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>
            <H2>IC nach Horizont</H2>
          </CardHeader>
          <CardBody>
            <BarChart
              categories={["4h", "8h", "24h", "72h", "120h"]}
              series={[{ name: "Mittel-IC", data: IC_BY_HORIZON, tone: "info" }]}
              height={220}
              valueSuffix=""
              beginAtZero={false}
            />
            <Text size="small" style={{ color: theme.text.tertiary }}>
              Y-Achse: IC (×100) · atr_percent → xs-Rendite · Block-Bootstrap 95 % CI
            </Text>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <H2>Monats-IC 24h</H2>
          </CardHeader>
          <CardBody>
            <LineChart
              categories={MONTHS}
              series={[{ name: "Monats-IC (×100)", data: MONTHLY_IC, tone: "info" }]}
              height={220}
              beginAtZero={false}
            />
            <Text size="small" style={{ color: theme.text.tertiary }}>
              Y-Achse: IC (×100) · X-Achse: Monat 2025-08–2026-07 · kein Vorzeichenwechsel
            </Text>
          </CardBody>
        </Card>
      </Grid>

      <Card>
        <CardHeader>
          <H2>Dezil-Kurve 24h — Mean vs. Median</H2>
        </CardHeader>
        <CardBody>
          <BarChart
            categories={DECILES}
            series={[
              { name: "Mittelwert (bp)", data: DECILE_MEAN_24H, tone: "info" },
              { name: "Median (bp)", data: DECILE_MEDIAN_24H, tone: "neutral" },
            ]}
            height={260}
            beginAtZero={false}
          />
          <Text size="small" style={{ color: theme.text.tertiary }}>
            Y-Achse: marktneutrale xs-Rendite (bp) · X-Achse: ATR-Dezil (D1 = niedrigste Vola)
          </Text>
        </CardBody>
      </Card>

      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>
            <H2>Bar-Replay Backtest</H2>
          </CardHeader>
          <CardBody>
            <BarChart
              categories={["Low-Vol", "High-Vol", "RSI", "72h", "Kombi", "Score", "Zufall"]}
              series={[
                {
                  name: "Mittel net R",
                  data: [0.0023, 0.0287, 0.0171, 0.0078, -0.0041, 0.0104, -0.0307],
                  tone: "info",
                },
              ]}
              height={200}
              beginAtZero={false}
            />
            <Table
              headers={["Strategie", "net R", "95 % CI", "N"]}
              columnAlign={["left", "right", "right", "right"]}
              rows={BACKTEST}
              striped
            />
            <Text size="small" style={{ color: theme.text.tertiary }}>
              Long-only Top-20 · Hold 24h · Stop 1,5×ATR · 0,05 % je Seite · 1 Position/Symbol
            </Text>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <H2>Break-even vs. Gebühren</H2>
          </CardHeader>
          <CardBody>
            <Table
              headers={["Hold", "net R (Low-Vol)", "Nach Gebühr"]}
              columnAlign={["left", "right", "center"]}
              rows={BREAK_EVEN}
              striped
            />
            <Text size="small" style={{ color: theme.text.tertiary, marginTop: 12 }}>
              Kein Horizont mit signifikant positivem net R. Typischer Stop ≈ 2,6 % → Gebühr ≈ 0,057 R.
              Gross R Low-Vol ≈ +0,038 R, Fees ≈ 0,036 R → net ≈ 0.
            </Text>
          </CardBody>
        </Card>
      </Grid>

      <Card>
        <CardHeader>
          <H2>Mean Reversion — nicht additiv mit Low-Vol</H2>
        </CardHeader>
        <CardBody>
          <Table
            headers={["Signal", "IC", "Top-N Mean xs", "sig."]}
            columnAlign={["left", "right", "right", "center"]}
            rows={COMBO}
            striped
          />
        </CardBody>
      </Card>

      <Callout tone="info" title="Empfehlung für DATAMIND">
        Low-Vol allein nicht deployen. Der robuste Charakter ist rangbasiert (Median/IC), nicht
        erwartungswertbasiert. Mean Reversion ist schwach positiv, aber auch im Bar-Replay nicht
        signifikant nach Gebühren. Keine Konfiguration cleared die Kostenhürde auf 366 Tagen 4h-Historie.
      </Callout>
    </Stack>
  );
}
