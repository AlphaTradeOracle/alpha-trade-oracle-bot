\pset tuples_only on
\pset format unaligned
SELECT 'WR2|' || wins || '|' || losses || '|' || ROUND((wr*100)::numeric,1) || '|' || pf FROM (
  SELECT COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
         COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
         CASE WHEN COUNT(*)>0 THEN COUNT(*) FILTER (WHERE realized_pnl>0)::float/COUNT(*) ELSE 0 END AS wr,
         CASE WHEN ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl<=0),0))>0
           THEN ROUND((COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl>0),0)/ABS(COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl<=0),0)))::numeric,2)
           ELSE 999 END AS pf
  FROM paper_positions WHERE status='closed') s;
SELECT 'TPLADDER|' || t1 || '|' || t2 || '|' || er || '|' || n || '|' || pnl || '|' || avgp FROM (
  SELECT tp1_filled::text t1, tp2_filled::text t2, COALESCE(exit_reason,'?') er, COUNT(*) n,
         ROUND(SUM(realized_pnl)::numeric,2) pnl, ROUND(AVG(realized_pnl)::numeric,2) avgp
  FROM paper_positions WHERE status='closed' GROUP BY 1,2,3) s ORDER BY t1,t2,er;
SELECT 'SCORE_WINLOSS|' || win || '|' || n || '|' || avgscore || '|' || avgdq || '|' || avgrr FROM (
  SELECT (p.realized_pnl > 0)::text win, COUNT(*) n, ROUND(AVG(s.score)::numeric,1) avgscore,
         ROUND(AVG(s.data_quality)::numeric,1) avgdq, ROUND(AVG(s.risk_reward_ratio)::numeric,2) avgrr
  FROM paper_positions p JOIN signals s ON s.id=p.signal_id WHERE p.status='closed' GROUP BY 1) q;
SELECT 'COMP|' || category || '|' || win || '|' || raw || '|' || wscore || '|' || n FROM (
  SELECT c.category, (p.realized_pnl > 0)::text win, ROUND(AVG(c.raw_score)::numeric,1) raw,
         ROUND(AVG(c.weighted_score)::numeric,2) wscore, COUNT(*) n
  FROM paper_positions p JOIN signal_score_components c ON c.signal_id=p.signal_id
  WHERE p.status='closed' GROUP BY 1,2) q ORDER BY category, win;
SELECT 'SYMBOL|' || symbol || '|' || direction || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT symbol, direction, COUNT(*) n, COUNT(*) FILTER (WHERE realized_pnl>0) w, ROUND(SUM(realized_pnl)::numeric,2) pnl
  FROM paper_positions WHERE status='closed' GROUP BY symbol, direction) q WHERE n >= 2 ORDER BY pnl;
SELECT 'HOUR|' || hr || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT EXTRACT(HOUR FROM opened_at AT TIME ZONE 'UTC')::int hr, COUNT(*) n,
         COUNT(*) FILTER (WHERE realized_pnl>0) w, ROUND(SUM(realized_pnl)::numeric,2) pnl
  FROM paper_positions WHERE status='closed' GROUP BY 1) q ORDER BY hr;
SELECT 'RETESTNOTE|' || COALESCE(note,'?') || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT substring(notes from 'note=([a-z_]+)') note, COUNT(*) n,
         COUNT(*) FILTER (WHERE realized_pnl>0) w, ROUND(SUM(realized_pnl)::numeric,2) pnl
  FROM paper_positions WHERE notes LIKE 'retest_filled%' AND status='closed' GROUP BY 1) q;
SELECT 'BARS|' || bars || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT (substring(notes from 'bars=([0-9]+)'))::int bars, COUNT(*) n,
         COUNT(*) FILTER (WHERE realized_pnl>0) w, ROUND(SUM(realized_pnl)::numeric,2) pnl
  FROM paper_positions WHERE notes LIKE 'retest_filled%' AND status='closed' GROUP BY 1) q ORDER BY bars;
SELECT 'DURATION|' || wl || '|' || er || '|' || hours || '|' || n FROM (
  SELECT CASE WHEN p.realized_pnl > 0 THEN 'win' ELSE 'loss' END wl, COALESCE(p.exit_reason,'?') er,
         ROUND(AVG(EXTRACT(EPOCH FROM (p.closed_at - p.opened_at))/3600)::numeric,1) hours, COUNT(*) n
  FROM paper_positions p WHERE p.status='closed' GROUP BY 1,2) q ORDER BY wl, er;
SELECT 'SKIPSTATUS|' || COALESCE(st,'?') || '|' || n FROM (
  SELECT substring(notes from 'status=([a-z_]+)') st, COUNT(*) n
  FROM paper_positions WHERE status='cancelled' GROUP BY 1) q;
SELECT 'SHORTSCORE|' || bucket || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT CASE WHEN s.score <= 18 THEN '<=18' ELSE '18-25' END bucket, COUNT(*) n,
         COUNT(*) FILTER (WHERE p.realized_pnl>0) w, ROUND(SUM(p.realized_pnl)::numeric,2) pnl
  FROM paper_positions p JOIN signals s ON s.id=p.signal_id
  WHERE p.status='closed' AND p.direction='STRONG_SHORT' GROUP BY 1) q ORDER BY bucket;
SELECT 'LONGSCORE|' || bucket || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT CASE WHEN s.score >= 82 THEN '>=82' ELSE '75-82' END bucket, COUNT(*) n,
         COUNT(*) FILTER (WHERE p.realized_pnl>0) w, ROUND(SUM(p.realized_pnl)::numeric,2) pnl
  FROM paper_positions p JOIN signals s ON s.id=p.signal_id
  WHERE p.status='closed' AND p.direction='STRONG_LONG' GROUP BY 1) q ORDER BY bucket;
SELECT 'DIREXIT|' || direction || '|' || er || '|' || n || '|' || w || '|' || pnl FROM (
  SELECT direction, COALESCE(exit_reason,'?') er, COUNT(*) n,
         COUNT(*) FILTER (WHERE realized_pnl>0) w, ROUND(SUM(realized_pnl)::numeric,2) pnl
  FROM paper_positions WHERE status='closed' GROUP BY 1,2) q ORDER BY direction, er;
