SELECT job_key, job_type, last_run_at, last_success_at, last_status, run_count,
       left(COALESCE(last_error,''), 160) AS err
FROM scheduled_jobs
ORDER BY last_run_at DESC NULLS LAST;
