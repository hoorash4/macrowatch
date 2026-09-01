-- Start incremental discovery on the day this collector is enabled. The next
-- successful run queries this date inclusively, so filings posted later on the
-- same day are still discovered by receipt number.

insert into earnings_v2.pipeline_state (
  source, operation, cursor, status, last_success_at, consecutive_failures, last_error
)
values (
  'korea_v2', 'daily_filings',
  jsonb_build_object(
    'last_checked_date', (now() at time zone 'Asia/Seoul')::date,
    'boundary_receipt_ids', '[]'::jsonb
  ),
  'ready', now(), 0, null
)
on conflict (source, operation) do nothing;
