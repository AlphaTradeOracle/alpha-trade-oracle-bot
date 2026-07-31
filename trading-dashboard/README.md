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
  components/   KPI, Charts, Trade-Tabellen, Badges
  pages/        Dashboard, Open, Pending, Closed, Analytics, Settings
  layout/       AppShell + Sidebar
  hooks/        useTrades / usePortfolio / useTradeFilters
  types/        Domain-Interfaces
  data/         Mock JSON (API-ready)
  utils/        Format, Score, Filter
```

## Spätere API-Anbindung

`useTrades` und `usePortfolio` laden heute JSON. Später genügt es, die Imports durch
`fetch('/api/...')` oder Exchange-Adapter (Binance / Bybit / Hyperliquid) zu ersetzen —
UI und Typen bleiben gleich.
