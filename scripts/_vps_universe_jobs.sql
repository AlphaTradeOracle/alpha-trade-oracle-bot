\d scheduled_jobs
SELECT id, job_type, job_key, status, last_run_at, next_run_at, last_error
FROM scheduled_jobs
ORDER BY last_run_at DESC NULLS LAST
LIMIT 30;

SELECT event_type, created_at, left(payload::text, 300) AS payload
FROM application_events
WHERE event_type ILIKE '%universe%'
   OR payload::text ILIKE '%universe%'
ORDER BY created_at DESC
LIMIT 20;
