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
      and (
        jobs.status in ('pending', 'retry')
        or (jobs.status = 'running' and jobs.claimed_at < now() - interval '30 minutes')
      )
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

create or replace function public.attach_earnings_open_dart_backfill_filings(p_filings jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_row jsonb;
  v_company_id uuid;
  v_job_id bigint;
  v_corp_code text;
  v_receipt_no text;
  v_year integer;
  v_report_code text;
  v_metadata jsonb;
  v_updated integer := 0;
  v_unmatched integer := 0;
begin
  if jsonb_typeof(p_filings) <> 'array' then
    raise exception 'OpenDART backfill filings must be a JSON array';
  end if;

  for v_row in select value from jsonb_array_elements(p_filings)
  loop
    v_corp_code := trim(coalesce(v_row->>'corp_code', ''));
    v_receipt_no := trim(coalesce(v_row->>'receipt_no', ''));
    v_year := nullif(v_row->>'business_year', '')::integer;
    v_report_code := trim(coalesce(v_row->>'report_code', ''));
    if v_corp_code !~ '^\d{8}$' or v_receipt_no !~ '^\d{14}$'
       or v_year is null or v_report_code not in ('11013', '11012', '11014', '11011') then
      raise exception 'Invalid OpenDART backfill filing row';
    end if;

    select company_id into v_company_id
    from public.earnings_company_identifiers
    where identifier_type = 'dart_corp_code'
      and identifier_value = v_corp_code
      and valid_to is null
    limit 1;

    select id into v_job_id
    from public.earnings_ingestion_jobs
    where source = 'open_dart' and job_kind = 'financial_period'
      and company_id = v_company_id and business_year = v_year
      and report_code = v_report_code
      and (
        status in ('pending', 'retry')
        or (status = 'running' and claimed_at < now() - interval '30 minutes')
      )
    order by priority desc, id asc
    limit 1;

    if v_job_id is null then
      v_unmatched := v_unmatched + 1;
      continue;
    end if;

    v_metadata := jsonb_strip_nulls(jsonb_build_object(
      'receipt_no', v_receipt_no,
      'filed_on', v_row->>'filed_on',
      'report_name', v_row->>'report_name',
      'is_correction', coalesce((v_row->>'is_correction')::boolean, false)
    ));

    update public.earnings_ingestion_jobs
    set priority = 100,
        reason = 'repair',
        metadata = v_metadata,
        status = case when status = 'running' then 'retry' else status end,
        claimed_at = case when status = 'running' then null else claimed_at end,
        available_at = least(available_at, now()),
        last_error = null,
        updated_at = now()
    where id = v_job_id;
    v_updated := v_updated + 1;
  end loop;

  return jsonb_build_object('updated', v_updated, 'unmatched', v_unmatched);
end;
$$;

revoke all on function public.attach_earnings_open_dart_backfill_filings(jsonb)
  from public, anon, authenticated;
grant execute on function public.attach_earnings_open_dart_backfill_filings(jsonb)
  to service_role;
