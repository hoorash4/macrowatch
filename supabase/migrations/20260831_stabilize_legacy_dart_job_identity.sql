-- Filing identity attachment predates the legacy worker and may replace a
-- job's metadata object. Identify the one-time archive queue by its fixed
-- 2002-2015 window and dedicated priority instead of mutable metadata.

create or replace function public.resolve_earnings_open_dart_legacy_filing_search(
  p_corp_code text, p_business_year integer, p_found_report_codes text[]
)
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_company_id uuid; v_updated integer;
begin
  select identifiers.company_id into v_company_id
  from public.earnings_company_identifiers identifiers
  where identifiers.identifier_type = 'dart_corp_code'
    and identifiers.identifier_value = p_corp_code
    and identifiers.valid_to is null limit 1;
  if v_company_id is null then return 0; end if;
  update public.earnings_ingestion_jobs jobs
  set status = 'completed', completed_at = now(), claimed_at = null,
      metadata = jobs.metadata || jsonb_build_object('outcome', 'no_filing'),
      updated_at = now()
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.company_id = v_company_id and jobs.business_year = p_business_year
    and jobs.priority = 10
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
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_company_id uuid; v_year integer; v_result jsonb;
begin
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running' and priority = 10
    and business_year between 2002 and 2015
    and claimed_at < now() - interval '30 minutes';
  select jobs.company_id, jobs.business_year into v_company_id, v_year
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.priority = 10 and jobs.business_year between 2002 and 2015
    and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
    and jobs.attempts < jobs.max_attempts
    and coalesce(jobs.metadata->>'receipt_no', '') <> ''
  order by jobs.priority desc, jobs.available_at asc, jobs.id asc
  for update skip locked limit 1;
  if v_company_id is null then return '[]'::jsonb; end if;
  with candidates as (
    select jobs.id from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.company_id = v_company_id and jobs.business_year = v_year
      and jobs.priority = 10 and jobs.business_year between 2002 and 2015
      and jobs.status in ('pending', 'retry') and jobs.available_at <= now()
      and jobs.attempts < jobs.max_attempts
      and coalesce(jobs.metadata->>'receipt_no', '') <> ''
    order by jobs.id for update skip locked
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
  ) order by claimed.id), '[]'::jsonb) into v_result
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
