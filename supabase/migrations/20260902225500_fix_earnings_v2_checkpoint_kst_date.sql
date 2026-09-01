-- Supabase sessions use UTC. Correct only the freshly initialized empty
-- checkpoint when KST has already crossed into the next calendar day.

update earnings_v2.pipeline_state
set cursor = jsonb_set(
      cursor,
      '{last_checked_date}',
      to_jsonb((now() at time zone 'Asia/Seoul')::date),
      true
    ),
    updated_at = now()
where source = 'korea_v2'
  and operation = 'daily_filings'
  and cursor->>'last_checked_date' = current_date::text
  and coalesce(cursor->'boundary_receipt_ids', '[]'::jsonb) = '[]'::jsonb
  and (now() at time zone 'Asia/Seoul')::date > current_date;
