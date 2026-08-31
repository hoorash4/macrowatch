-- Keep one company chronological while allowing unrelated companies to be
-- processed concurrently. The worker claims batches sequentially, so a row
-- marked running by one claim is visible before the next claim begins.
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
    and not exists (
      select 1 from public.earnings_ingestion_jobs running
      where running.source = jobs.source
        and running.job_kind = jobs.job_kind
        and running.company_id = jobs.company_id
        and running.status = 'running'
    )
  order by jobs.business_year asc, jobs.company_id asc, jobs.id asc
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
