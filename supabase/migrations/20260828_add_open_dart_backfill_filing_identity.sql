-- Return only tracked companies whose queued historical periods still lack
-- official OpenDART filing identity. The service-role worker uses this compact
-- list to fetch official filing metadata before retrying financial statements.

create or replace function public.list_earnings_open_dart_identity_gaps()
returns jsonb
language sql
security definer
set search_path = public, pg_temp
as $$
  with gaps as (
    select
      jobs.company_id,
      identifiers.identifier_value as corp_code,
      array_agg(distinct jobs.business_year order by jobs.business_year) as years
    from public.earnings_ingestion_jobs jobs
    join public.earnings_company_identifiers identifiers
      on identifiers.company_id = jobs.company_id
     and identifiers.identifier_type = 'dart_corp_code'
     and identifiers.valid_to is null
    where jobs.source = 'open_dart'
      and jobs.job_kind = 'financial_period'
      and jobs.status in ('pending', 'retry')
      and jobs.attempts < jobs.max_attempts
      and coalesce(jobs.metadata->>'receipt_no', '') = ''
      and coalesce(jobs.metadata->>'filed_on', '') = ''
      -- Do not refetch a company's current-year filing list every day for a
      -- quarter whose statutory filing window has not arrived yet.
      and case jobs.report_code
        when '11013' then make_date(jobs.business_year, 5, 31)
        when '11012' then make_date(jobs.business_year, 8, 31)
        when '11014' then make_date(jobs.business_year, 11, 30)
        when '11011' then make_date(jobs.business_year + 1, 4, 30)
      end <= current_date
    group by jobs.company_id, identifiers.identifier_value
  )
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'company_id', company_id,
        'corp_code', corp_code,
        'years', to_jsonb(years)
      )
      order by corp_code
    ),
    '[]'::jsonb
  )
  from gaps;
$$;

revoke all on function public.list_earnings_open_dart_identity_gaps()
  from public, anon, authenticated;
grant execute on function public.list_earnings_open_dart_identity_gaps()
  to service_role;
