-- The first quarantine recovery run used the older worker, which stored only
-- the exception class and exhausted retries before the safe RPC diagnostic was
-- deployed. Re-open only those one-time repair jobs; subsequent failures carry
-- the sanitized PostgREST status/code and are not reset by this migration.

update public.earnings_ingestion_jobs
set status = 'retry',
    attempts = 0,
    available_at = now(),
    claimed_at = null,
    completed_at = null,
    last_error = null,
    updated_at = now()
where source = 'open_dart'
  and job_kind = 'financial_period'
  and collector_variant = 'legacy_archive'
  and status = 'failed'
  and last_error = 'EarningsStoreError'
  and metadata->>'repair_reason' = 'legacy_zero_quarter_quarantine';
