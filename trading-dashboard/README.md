# Alpha Desk — Trading Dashboard

Lokales Trading-Dashboard (Vite + React + TypeScript + Tailwind).  
Keine Login-Funktion, keine Datenbank — Daten kommen aus JSON unter `src/data/`.

## Start

```bash
cd trading-dashboard
npm install
npm run dev
```

App: [http://127.0.0.1:5173](http://127.0.0.1:5173)

## Struktur

```
src/
  components/   KPI, Charts, Trade-Tabellen, Badges, Brand, Icons, UI
  pages/        Dashboard, Open, Pending, Closed, Analytics, Settings
  layout/       AppShell + Sidebar + Footer
  hooks/        useTrades / usePortfolio / useTradeFilters / useSettings
  types/        Domain-Interfaces
  config/       branding.ts, socialLinks.ts
  data/         Mock JSON (API-ready)
  utils/        Format, Score, Filter
```

## Logo austauschen

Eigene Datei nach `public/brand/logo.png` legen — fertig.
Pfad und Projektname stehen zentral in `src/config/branding.ts`;
solange keine Datei vorhanden ist, greift `logo-fallback.svg`.

## Social Links

Alle Links zentral in `src/config/socialLinks.ts` (aktuell Platzhalter `#`).

## Statische Vorschau

```bash
npm run build
python3 scripts/serve-static.py 5173
```

Der Server liefert `dist/` inkl. SPA-Fallback, damit Deep Links funktionieren.

## Spätere API-Anbindung

`useTrades` und `usePortfolio` laden heute JSON. Später genügt es, die Imports durch
`fetch('/api/...')` oder Exchange-Adapter (Binance / Bybit / Hyperliquid) zu ersetzen —
UI und Typen bleiben gleich.
