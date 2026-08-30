-- Separate pre-XBRL filing archives from the structured OpenDART worker.
-- The structured account endpoints reliably populate MacroWatch from 2016;
-- 2002-2015 must be read from each filing's official document.xml archive.

create or replace function public.enqueue_earnings_open_dart_legacy_backfill()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_inserted integer;
begin
  insert into public.earnings_ingestion_jobs
    (source, job_kind, company_id, business_year, report_code, reason, priority, metadata)
  select
    'open_dart', 'financial_period', companies.company_id, years.business_year,
    reports.report_code, 'new_company', 10,
    jsonb_build_object('backfill', true, 'legacy_archive', true)
  from (
    select distinct memberships.company_id
    from public.earnings_index_memberships memberships
    join public.earnings_indices indices on indices.index_id = memberships.index_id
    where memberships.effective_to is null and indices.country = 'KR'
  ) companies
  cross join lateral generate_series(2002, 2015) years(business_year)
  cross join (values ('11013'), ('11012'), ('11014'), ('11011')) reports(report_code)
  where exists (
    select 1 from public.earnings_company_identifiers identifiers
    where identifiers.company_id = companies.company_id
      and identifiers.identifier_type = 'dart_corp_code'
      and identifiers.valid_to is null
  )
  and not exists (
    select 1 from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart'
      and jobs.job_kind = 'financial_period'
      and jobs.company_id = companies.company_id
      and jobs.business_year = years.business_year
      and jobs.report_code = reports.report_code
      and jobs.metadata @> '{"legacy_archive": true}'::jsonb
  )
  and not exists (
    select 1 from public.earnings_quarterly_financials financials
    where financials.company_id = companies.company_id
      and financials.fiscal_year = years.business_year
      and financials.fiscal_quarter = case reports.report_code
        when '11013' then 1 when '11012' then 2
        when '11014' then 3 when '11011' then 4 end
  );
  get diagnostics v_inserted = row_count;
  return v_inserted;
end;
$$;

revoke all on function public.enqueue_earnings_open_dart_legacy_backfill()
  from public, anon, authenticated;
grant execute on function public.enqueue_earnings_open_dart_legacy_backfill()
  to service_role;

create or replace function public.resolve_earnings_open_dart_legacy_filing_search(
  p_corp_code text,
  p_business_year integer,
  p_found_report_codes text[]
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_company_id uuid;
  v_updated integer;
begin
  select identifiers.company_id into v_company_id
  from public.earnings_company_identifiers identifiers
  where identifiers.identifier_type = 'dart_corp_code'
    and identifiers.identifier_value = p_corp_code
    and identifiers.valid_to is null
  limit 1;
  if v_company_id is null then
    return 0;
  end if;

  update public.earnings_ingestion_jobs jobs
  set status = 'completed', completed_at = now(), claimed_at = null,
      metadata = jobs.metadata || jsonb_build_object('outcome', 'no_filing'),
      updated_at = now()
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.company_id = v_company_id
    and jobs.business_year = p_business_year
    and jobs.metadata @> '{"legacy_archive": true}'::jsonb
    and jobs.status in ('pending', 'retry')
    and not (jobs.report_code = any(coalesce(p_found_report_codes, array[]::text[])));
  get diagnostics v_updated = row_count;
  return v_updated;
end;
$$;

revoke all on function public.resolve_earnings_open_dart_legacy_filing_search(text, integer, text[])
  from public, anon, authenticated;
grant execute on function public.resolve_earnings_open_dart_legacy_filing_search(text, integer, text[])
  to service_role;

create or replace function public.claim_earnings_open_dart_legacy_jobs()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_company_id uuid;
  v_year integer;
  v_result jsonb;
begin
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and metadata @> '{"legacy_archive": true}'::jsonb
    and claimed_at < now() - interval '30 minutes';

  select jobs.company_id, jobs.business_year
    into v_company_id, v_year
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.metadata @> '{"legacy_archive": true}'::jsonb
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and coalesce(jobs.metadata->>'receipt_no', '') <> ''
  order by jobs.priority desc, jobs.available_at asc, jobs.id asc
  for update skip locked
  limit 1;

  if v_company_id is null then
    return '[]'::jsonb;
  end if;

  with candidates as (
    select jobs.id
    from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = v_company_id and jobs.business_year = v_year
      and jobs.metadata @> '{"legacy_archive": true}'::jsonb
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
      and coalesce(jobs.metadata->>'receipt_no', '') <> ''
    order by jobs.id
    for update skip locked
  ), claimed as (
    update public.earnings_ingestion_jobs jobs
    set status = 'running', attempts = attempts + 1, claimed_at = now(),
        last_error = null, updated_at = now()
    from candidates where jobs.id = candidates.id
    returning jobs.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', claimed.id,
    'company_id', claimed.company_id,
    'business_year', claimed.business_year,
    'report_code', claimed.report_code,
    'reason', claimed.reason,
    'attempts', claimed.attempts,
    'metadata', claimed.metadata,
    'corp_code', identifiers.identifier_value,
    'ticker', companies.ticker,
    'company_name', companies.company_name
  ) order by claimed.id), '[]'::jsonb)
  into v_result
  from claimed
  join public.earnings_companies companies on companies.id = claimed.company_id
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = claimed.company_id
   and identifiers.identifier_type = 'dart_corp_code'
   and identifiers.valid_to is null;
  return v_result;
end;
$$;

revoke all on function public.claim_earnings_open_dart_legacy_jobs()
  from public, anon, authenticated;
grant execute on function public.claim_earnings_open_dart_legacy_jobs()
  to service_role;

-- Keep the structured batch worker away from pre-XBRL archive jobs.
create or replace function public.claim_earnings_open_dart_jobs(p_limit integer default 100)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_year integer;
  v_report_code text;
  v_result jsonb;
begin
  if p_limit < 1 or p_limit > 100 then
    raise exception 'OpenDART claim limit must be between 1 and 100';
  end if;
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and not (metadata @> '{"legacy_archive": true}'::jsonb)
    and claimed_at < now() - interval '30 minutes';

  select jobs.business_year, jobs.report_code into v_year, v_report_code
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and not (jobs.metadata @> '{"legacy_archive": true}'::jsonb)
    and jobs.business_year >= 2016
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and exists (
      select 1 from public.earnings_company_identifiers identifiers
      where identifiers.company_id = jobs.company_id
        and identifiers.identifier_type = 'dart_corp_code'
        and identifiers.valid_to is null
    )
  order by jobs.priority desc, jobs.available_at asc, jobs.id asc
  for update skip locked limit 1;
  if v_year is null then return '[]'::jsonb; end if;

  with candidates as (
    select jobs.id
    from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and not (jobs.metadata @> '{"legacy_archive": true}'::jsonb)
      and jobs.business_year = v_year and jobs.report_code = v_report_code
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
    order by jobs.priority desc, jobs.available_at asc, jobs.id asc
    for update skip locked limit p_limit
  ), claimed as (
    update public.earnings_ingestion_jobs jobs
    set status = 'running', attempts = attempts + 1, claimed_at = now(),
        last_error = null, updated_at = now()
    from candidates where jobs.id = candidates.id returning jobs.*
  )
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', claimed.id, 'company_id', claimed.company_id,
    'business_year', claimed.business_year, 'report_code', claimed.report_code,
    'reason', claimed.reason, 'attempts', claimed.attempts,
    'metadata', claimed.metadata, 'corp_code', identifiers.identifier_value,
    'ticker', companies.ticker, 'company_name', companies.company_name
  ) order by claimed.priority desc, claimed.id asc), '[]'::jsonb)
  into v_result
  from claimed
  join public.earnings_companies companies on companies.id = claimed.company_id
  join public.earnings_company_identifiers identifiers
    on identifiers.company_id = claimed.company_id
   and identifiers.identifier_type = 'dart_corp_code'
   and identifiers.valid_to is null;
  return v_result;
end;
$$;

revoke all on function public.claim_earnings_open_dart_jobs(integer)
  from public, anon, authenticated;
grant execute on function public.claim_earnings_open_dart_jobs(integer)
  to service_role;
