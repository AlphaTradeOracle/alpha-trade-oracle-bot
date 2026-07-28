"""One-off: build assets-database.canvas.tsx from VPS export."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "assets_db_export.txt"
OUT = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot\canvases\assets-database.canvas.tsx"
)

lines = [
    line.strip()
    for line in EXPORT.read_text(encoding="utf-8").splitlines()
    if line.strip() and "|" in line and not line.startswith("Output") and not line.startswith("Field")
]
header = lines[0].split("|")
rows: list[dict[str, object]] = []
for line in lines[1:]:
    d = dict(zip(header, line.split("|"), strict=True))
    rows.append(
        {
            "id": int(d["id"]),
            "symbol": d["symbol"],
            "base_asset": d["base_asset"],
            "quote_asset": d["quote_asset"],
            "exchange": d["exchange"],
            "price_precision": int(d["price_precision"]),
            "quantity_precision": int(d["quantity_precision"]),
            "is_active": d["is_active"] == "t",
            "coingecko_id": d["coingecko_id"] or "",
            "market_cap_rank": int(d["market_cap_rank"]) if d["market_cap_rank"] else None,
            "market_cap_usd": float(d["market_cap_usd"]) if d["market_cap_usd"] else None,
            "in_universe": d["in_universe"] == "t",
            "last_ranked_at": d["last_ranked_at"] or "",
            "last_scanned_at": d["last_scanned_at"] or "",
            "created_at": d["created_at"],
            "updated_at": d["updated_at"] or "",
            "candle_count": int(d["candle_count"]),
            "snapshot_count": int(d["snapshot_count"]),
            "signal_count": int(d["signal_count"]),
        }
    )

data_json = json.dumps(rows, ensure_ascii=False, indent=2)
template = '''import {
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
  TextInput,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

const ASSETS = __DATA__ as const;

type AssetRow = (typeof ASSETS)[number];

function fmtMcap(value: number | null): string {
  if (value == null) return "—";
  if (value >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  return value.toLocaleString();
}

function fmtTs(value: string): string {
  if (!value) return "—";
  return value.replace("+00", " UTC").slice(0, 19);
}

export default function AssetsDatabaseCanvas() {
  const theme = useHostTheme();
  const [query, setQuery] = useCanvasState("query", "");
  const [onlyUniverse, setOnlyUniverse] = useCanvasState("onlyUniverse", "all");

  const q = query.trim().toLowerCase();
  const filtered = ASSETS.filter((row: AssetRow) => {
    if (onlyUniverse === "yes" && !row.in_universe) return false;
    if (!q) return true;
    return [row.symbol, row.base_asset, row.coingecko_id, row.exchange].some((part) =>
      part.toLowerCase().includes(q),
    );
  });

  const totals = {
    assets: ASSETS.length,
    universe: ASSETS.filter((r) => r.in_universe).length,
    candles: ASSETS.reduce((s, r) => s + r.candle_count, 0),
    snapshots: ASSETS.reduce((s, r) => s + r.snapshot_count, 0),
    signals: ASSETS.reduce((s, r) => s + r.signal_count, 0),
  };

  const tableRows = filtered.map((row: AssetRow) => [
    row.market_cap_rank ?? "—",
    row.symbol,
    row.base_asset,
    row.quote_asset,
    row.exchange,
    row.in_universe ? "ja" : "nein",
    fmtMcap(row.market_cap_usd),
    row.candle_count.toLocaleString(),
    row.snapshot_count,
    row.signal_count,
    row.is_active ? "aktiv" : "inaktiv",
    row.coingecko_id || "—",
    row.price_precision,
    row.quantity_precision,
    fmtTs(row.last_scanned_at),
    fmtTs(row.last_ranked_at),
    row.id,
    fmtTs(row.created_at),
    fmtTs(row.updated_at),
  ]);

  return (
    <Stack gap={16}>
      <Stack gap={6}>
        <H1>Assets in der Datenbank</H1>
        <Text tone="secondary">
          Production VPS · assets + aggregierte Kerzen/Indikatoren/Signale · Stand 2026-07-28
        </Text>
      </Stack>

      <Grid columns={{ sm: 2, md: 5 }} gap={12}>
        <Stat label="Assets gesamt" value={String(totals.assets)} />
        <Stat label="Im Universe" value={String(totals.universe)} />
        <Stat label="Kerzen" value={totals.candles.toLocaleString()} />
        <Stat label="Indikator-Snapshots" value={totals.snapshots.toLocaleString()} />
        <Stat label="Signale" value={String(totals.signals)} />
      </Grid>

      <Card>
        <CardHeader title="Filter" />
        <CardBody>
          <Stack gap={10}>
            <TextInput
              value={query}
              onChange={setQuery}
              placeholder="Symbol, Base, CoinGecko-ID oder Exchange suchen…"
              type="search"
            />
            <Stack direction="row" gap={8}>
              <Pill tone={onlyUniverse === "all" ? "info" : "neutral"} onClick={() => setOnlyUniverse("all")}>
                Alle ({totals.assets})
              </Pill>
              <Pill tone={onlyUniverse === "yes" ? "info" : "neutral"} onClick={() => setOnlyUniverse("yes")}>
                Nur Universe ({totals.universe})
              </Pill>
            </Stack>
            <Text tone="secondary">{filtered.length} von {ASSETS.length} Coins angezeigt</Text>
          </Stack>
        </CardBody>
      </Card>

      <Stack gap={8}>
        <H2>Alle Spalten ({filtered.length} Coins)</H2>
        <Table
          headers={[
            "Rank",
            "Symbol",
            "Base",
            "Quote",
            "Exchange",
            "Universe",
            "Market Cap USD",
            "Kerzen",
            "Snapshots",
            "Signale",
            "Aktiv",
            "CoinGecko ID",
            "Preis-Dec",
            "Qty-Dec",
            "Zuletzt gescannt",
            "Zuletzt gerankt",
            "DB-ID",
            "Created",
            "Updated",
          ]}
          rows={tableRows}
          columnAlign={[
            "right",
            "left",
            "left",
            "left",
            "left",
            "center",
            "right",
            "right",
            "right",
            "right",
            "center",
            "left",
            "right",
            "right",
            "left",
            "left",
            "right",
            "left",
            "left",
          ]}
          striped
          stickyHeader
          framed
          emptyMessage="Keine Coins für diesen Filter"
        />
      </Stack>

      <Text tone="secondary" style={{ color: theme.textSecondary }}>
        Pro Coin gibt es zusätzlich market_candles (OHLCV je Timeframe) und indicator_snapshots (alle
        Indikator-Spalten je TF). Hier aggregiert als Kerzen/Snapshots/Signale.
      </Text>
    </Stack>
  );
}
'''

OUT.write_text(template.replace("__DATA__", data_json), encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes, {len(rows)} rows)")
