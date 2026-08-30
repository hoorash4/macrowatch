-- This migration must remain after every historical definition of the claim
-- RPC.  The structured worker may never consume legacy filing-archive jobs.
create or replace function public.claim_earnings_open_dart_jobs(p_limit integer default 100)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_year integer; v_report_code text; v_result jsonb;
begin
  if p_limit < 1 or p_limit > 100 then
    raise exception 'OpenDART claim limit must be between 1 and 100';
  end if;
  update public.earnings_ingestion_jobs
  set status = 'retry', claimed_at = null, available_at = now(), updated_at = now(),
      last_error = 'Worker lease expired'
  where source = 'open_dart' and status = 'running'
    and collector_variant = 'structured'
    and claimed_at < now() - interval '30 minutes';
  select jobs.business_year, jobs.report_code into v_year, v_report_code
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.collector_variant = 'structured'
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
    select jobs.id from public.earnings_ingestion_jobs jobs
    where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
      and jobs.collector_variant = 'structured'
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
  ) order by claimed.priority desc, claimed.id asc), '[]'::jsonb) into v_result
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
