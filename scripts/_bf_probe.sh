#!/usr/bin/env bash
cd /opt/alpha-trade-oracle-bot
echo "--- container ---"
docker ps -a --filter name=alpha-backfill --format '{{.Status}}'
echo "--- tail ---"
docker logs alpha-backfill 2>&1 | tail -n 2
echo "--- 1h report ---"
python3 -c "import json;d=json.load(open('exports/backfill_1h_report.json'));print(json.dumps(d['stats'],indent=1));print('duration_s',d['duration_seconds'],'interrupted',d['interrupted'],'failures',len(d['failures']))"
echo "--- 4h report ---"
python3 -c "import json;d=json.load(open('exports/backfill_4h_report.json'));print(json.dumps(d['stats'],indent=1));print('duration_s',d['duration_seconds'],'interrupted',d['interrupted'],'failures',len(d['failures']))" 2>/dev/null || echo "4h report noch nicht geschrieben"

q() { docker exec alpha-trade-oracle-postgres psql -U alpha_trade_oracle -d alpha_trade_oracle -At -F'|' -c "$1"; }

echo "--- coverage per timeframe ---"
q "select timeframe, count(*) bars, count(distinct asset_id) assets, min(open_time) min_t, max(open_time) max_t from market_candles group by timeframe order by timeframe;"

echo "--- 1h: assets reaching 6 months (>=4300 bars) ---"
q "with c as (select a.symbol, count(*) n, min(m.open_time) oldest from assets a join market_candles m on m.asset_id=a.id and m.timeframe='1h' where a.in_universe and a.is_active group by a.symbol) select count(*) total, count(*) filter (where n>=4300) deep, count(*) filter (where n<4300) shallow, min(n), round(avg(n)), max(n) from c;"

echo "--- 4h: assets reaching 12 months (>=2150 bars) ---"
q "with c as (select a.symbol, count(*) n from assets a join market_candles m on m.asset_id=a.id and m.timeframe='4h' where a.in_universe and a.is_active group by a.symbol) select count(*) total, count(*) filter (where n>=2150) deep, count(*) filter (where n<2150) shallow, min(n), round(avg(n)), max(n) from c;"

echo "--- 1h shallow symbols (top 25) ---"
q "with c as (select a.symbol, count(*) n, min(m.open_time) oldest from assets a join market_candles m on m.asset_id=a.id and m.timeframe='1h' where a.in_universe and a.is_active group by a.symbol) select symbol, n, oldest from c where n<4300 order by n asc limit 25;"

echo "--- universe assets without 1h candles ---"
q "select a.symbol from assets a where a.in_universe and a.is_active and not exists (select 1 from market_candles m where m.asset_id=a.id and m.timeframe='1h');"

echo "--- duplicate check ---"
q "select count(*) from (select asset_id, timeframe, open_time from market_candles group by 1,2,3 having count(*)>1) x;"

echo "--- db size / disk ---"
q "select pg_size_pretty(pg_database_size('alpha_trade_oracle'));"
df -h / | tail -n 1
