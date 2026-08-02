-- Extra cleanup beyond app data prune:
-- signals for out-of-universe assets not referenced by paper
-- old application_events (>30d)

BEGIN;

WITH doomed AS (
  SELECT s.id
  FROM signals s
  JOIN assets a ON a.id = s.asset_id
  WHERE a.in_universe IS NOT TRUE
    AND NOT EXISTS (
      SELECT 1 FROM paper_positions p WHERE p.signal_id = s.id
    )
)
DELETE FROM signals s
USING doomed d
WHERE s.id = d.id;

DELETE FROM application_events
WHERE created_at < NOW() - INTERVAL '30 days';

COMMIT;
