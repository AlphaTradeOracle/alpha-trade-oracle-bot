UPDATE scheduled_jobs
SET is_enabled = false
WHERE job_key = 'market_scan:60m';

UPDATE scheduled_jobs
SET next_run_at = NOW(), last_status = NULL
WHERE job_key = 'market_scan:30m';

SELECT job_key, interval_seconds, last_run_at, next_run_at, run_count, is_enabled
FROM scheduled_jobs
WHERE job_key LIKE 'market_scan%';
