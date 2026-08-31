-- Final replay-safe earnings definitions. The deployment workflow replays
-- historical idempotent schema files, so this file must remain last and must
-- contain no one-time repair DML.

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
    and jobs.collector_variant = 'structured' and jobs.business_year >= 2016
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
grant execute on function public.claim_earnings_open_dart_jobs(integer) to service_role;

create or replace function public.apply_earnings_job_review_outcome()
returns trigger language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_quarter smallint;
begin
  if new.source <> 'open_dart'
     or coalesce(new.metadata->>'outcome', '') <> 'review_required' then
    return new;
  end if;
  v_quarter := case new.report_code
    when '11013' then 1 when '11012' then 2
    when '11014' then 3 when '11011' then 4 else null end;
  if v_quarter is not null then
    update public.earnings_quarterly_financials
    set quality_status = 'review_required',
        canonical_version = canonical_version + 1,
        calculated_at = now(), updated_at = now()
    where company_id = new.company_id and fiscal_year = new.business_year
      and fiscal_quarter = v_quarter;
  end if;
  return new;
end;
$$;
revoke all on function public.apply_earnings_job_review_outcome()
  from public, anon, authenticated;
drop trigger if exists earnings_job_review_outcome_trg on public.earnings_ingestion_jobs;
create trigger earnings_job_review_outcome_trg
after update of metadata on public.earnings_ingestion_jobs
for each row execute function public.apply_earnings_job_review_outcome();

create or replace function public.remove_invalid_earnings_growth_metric()
returns trigger language plpgsql security definer
set search_path = public, pg_temp
as $$
begin
  if new.quality_status <> 'complete' then
    delete from public.earnings_quarterly_growth_metrics
    where company_id = new.company_id and fiscal_year = new.fiscal_year
      and fiscal_quarter = new.fiscal_quarter;
  end if;
  return new;
end;
$$;
revoke all on function public.remove_invalid_earnings_growth_metric()
  from public, anon, authenticated;
drop trigger if exists earnings_financial_quality_cleanup_trg
  on public.earnings_quarterly_financials;
create trigger earnings_financial_quality_cleanup_trg
after insert or update of quality_status on public.earnings_quarterly_financials
for each row execute function public.remove_invalid_earnings_growth_metric();

create or replace function public.prune_invalid_earnings_growth_metrics()
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_deleted integer;
begin
  delete from public.earnings_quarterly_growth_metrics metrics
  where not exists (
    select 1 from public.earnings_quarterly_financials financials
    where financials.company_id = metrics.company_id
      and financials.fiscal_year = metrics.fiscal_year
      and financials.fiscal_quarter = metrics.fiscal_quarter
      and financials.quality_status = 'complete'
  );
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;
revoke all on function public.prune_invalid_earnings_growth_metrics()
  from public, anon, authenticated;
grant execute on function public.prune_invalid_earnings_growth_metrics() to service_role;

create or replace function public.prune_stale_earnings_market_derivatives(
  p_calculated_at timestamptz
)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_metrics integer; v_breadth integer;
begin
  delete from public.earnings_market_quarterly_metrics
  where calculated_at is distinct from p_calculated_at;
  get diagnostics v_metrics = row_count;
  delete from public.earnings_market_quarterly_breadth
  where calculated_at is distinct from p_calculated_at;
  get diagnostics v_breadth = row_count;
  return jsonb_build_object('metrics', v_metrics, 'breadth', v_breadth);
end;
$$;
revoke all on function public.prune_stale_earnings_market_derivatives(timestamptz)
  from public, anon, authenticated;
grant execute on function public.prune_stale_earnings_market_derivatives(timestamptz)
  to service_role;

alter table public.earnings_market_quarterly_metrics
  drop constraint if exists earnings_market_quarterly_metrics_yoy_state_check;
alter table public.earnings_market_quarterly_metrics
  add constraint earnings_market_quarterly_metrics_yoy_state_check
  check (yoy_state in (
    'normal', 'black_turn', 'red_turn', 'loss_narrowing', 'loss_widening',
    'loss_unchanged', 'from_zero', 'missing_prior_snapshot',
    'insufficient_coverage'
  ));

-- Preserve legitimate pre-revenue companies while blocking canonical rows
-- that cannot represent a real quarterly income statement.
create or replace function public.enforce_earnings_canonical_numeric_quality()
returns trigger language plpgsql
set search_path = public, pg_temp
as $$
begin
  if new.revenue < 0
     or (new.revenue = 0 and new.operating_income = 0 and new.net_income = 0) then
    new.quality_status := 'review_required';
  end if;
  return new;
end;
$$;

drop trigger if exists earnings_canonical_numeric_quality_trg
  on public.earnings_quarterly_financials;
create trigger earnings_canonical_numeric_quality_trg
before insert or update of revenue, operating_income, net_income, quality_status
on public.earnings_quarterly_financials
for each row execute function public.enforce_earnings_canonical_numeric_quality();

revoke all on function public.enforce_earnings_canonical_numeric_quality()
  from public, anon, authenticated;
grant execute on function public.enforce_earnings_canonical_numeric_quality()
  to service_role;

-- A worker version upgrade must revalidate already completed legacy rows once.
-- Filing metadata becomes the durable marker, so later runs remain idempotent.
create or replace function public.requeue_unvalidated_legacy_earnings_jobs()
returns integer language plpgsql security definer
set search_path = public, pg_temp
as $$
declare v_count integer;
begin
  update public.earnings_ingestion_jobs jobs
  set status='retry', attempts=0, available_at=now(), claimed_at=null,
      completed_at=null, last_error=null,
      metadata=jobs.metadata || jsonb_build_object(
        'quality_revalidation', true,
        'quality_revalidation_version', 1
      ),
      updated_at=now()
  where jobs.collector_variant='legacy_archive'
    and jobs.status='completed'
    and jobs.business_year between 2002 and 2015
    and coalesce(jobs.metadata->>'receipt_no','')<>''
    and not exists (
      select 1 from public.earnings_filings filings
      where filings.source='open_dart'
        and filings.source_filing_id=jobs.metadata->>'receipt_no'
        and filings.metadata ? 'quality_issues'
    );
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.requeue_unvalidated_legacy_earnings_jobs()
  from public, anon, authenticated;
grant execute on function public.requeue_unvalidated_legacy_earnings_jobs()
  to service_role;

create index if not exists earnings_quarterly_financials_source_filing_idx
  on public.earnings_quarterly_financials (source_filing_id);
