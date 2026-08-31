-- Keep source-review rows out of every derived series and make the OpenDART
-- completion outcome authoritative. Earlier RPC versions inferred quality
-- only from missing fields, so a complete-looking but implausible quarter was
-- accidentally published even when the worker returned review_required.

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

drop trigger if exists earnings_job_review_outcome_trg
  on public.earnings_ingestion_jobs;
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
grant execute on function public.prune_invalid_earnings_growth_metrics()
  to service_role;

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

-- Quarantine only official OpenDART rows that are structurally impossible,
-- obvious company-history outliers in the legacy window, or the recent rows
-- already confirmed by the audit. No amount is edited or fabricated.
with company_stats as (
  select financials.company_id, financials.currency,
    percentile_cont(0.5) within group (order by financials.revenue)
      filter (where financials.revenue > 0) as median_revenue,
    count(*) filter (where financials.revenue > 0) as positive_count
  from public.earnings_quarterly_financials financials
  where financials.quality_status = 'complete'
  group by financials.company_id, financials.currency
), quarantined as (
  update public.earnings_quarterly_financials financials
  set quality_status = 'review_required',
      canonical_version = canonical_version + 1,
      calculated_at = now(), updated_at = now()
  from public.earnings_filings filings,
       public.earnings_companies companies,
       company_stats stats
  where filings.id = financials.source_filing_id
    and filings.source = 'open_dart'
    and companies.id = financials.company_id
    and stats.company_id = financials.company_id
    and stats.currency = financials.currency
    and (
      financials.revenue <= 0
      or (financials.currency = 'KRW' and financials.revenue > 1000000000000000)
      or (financials.currency = 'USD' and financials.revenue > 2000000000000)
      or (
        financials.fiscal_year between 2002 and 2015
        and stats.positive_count >= 6 and financials.revenue > 0
        and (
          financials.revenue > stats.median_revenue * 50
          or financials.revenue < stats.median_revenue / 50
        )
      )
      or (companies.ticker = '007720' and financials.fiscal_year = 2024
          and financials.fiscal_quarter in (3, 4))
      or (companies.ticker in ('005930', '000660') and financials.fiscal_year = 2026
          and financials.fiscal_quarter in (1, 2))
    )
  returning financials.company_id, financials.fiscal_year,
            financials.fiscal_quarter
), repair_jobs as (
  select distinct on (jobs.company_id, jobs.business_year, jobs.report_code)
    jobs.id
  from public.earnings_ingestion_jobs jobs
  join quarantined on jobs.company_id = quarantined.company_id
    and jobs.business_year = quarantined.fiscal_year
    and jobs.report_code = case quarantined.fiscal_quarter
      when 1 then '11013' when 2 then '11012'
      when 3 then '11014' when 4 then '11011' end
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
  order by jobs.company_id, jobs.business_year, jobs.report_code,
    case when jobs.status in ('pending', 'retry', 'running') then 0 else 1 end,
    jobs.id desc
)
update public.earnings_ingestion_jobs jobs
set status = 'pending', attempts = 0, claimed_at = null, completed_at = null,
    available_at = now(), last_error = null,
    collector_variant = case when jobs.business_year <= 2015
      then 'legacy_archive' else 'structured' end,
    metadata = jobs.metadata || jsonb_build_object(
      'repair_reason', 'canonical_quality_audit_20260831'
    ),
    updated_at = now()
from repair_jobs
where jobs.id = repair_jobs.id;

-- Rebuild every legacy company-year from all cumulative filings. This fills
-- missing Q4 values and prevents a repaired Q2/Q3 from being mixed with the
-- previous parser generation. Existing normal canonical values remain visible
-- until their official replacement is successfully completed.
with repair_jobs as (
  select distinct on (jobs.company_id, jobs.business_year, jobs.report_code)
    jobs.id
  from public.earnings_ingestion_jobs jobs
  where jobs.source = 'open_dart' and jobs.job_kind = 'financial_period'
    and jobs.business_year between 2002 and 2015
    and coalesce(jobs.metadata->>'receipt_no', '') <> ''
  order by jobs.company_id, jobs.business_year, jobs.report_code,
    case when jobs.status in ('pending', 'retry', 'running') then 0 else 1 end,
    jobs.id desc
)
update public.earnings_ingestion_jobs jobs
set status = 'pending', attempts = 0, claimed_at = null, completed_at = null,
    available_at = now(), last_error = null, collector_variant = 'legacy_archive',
    metadata = jobs.metadata || jsonb_build_object(
      'legacy_archive', true,
      'repair_reason', 'full_legacy_company_year_rebuild_20260831'
    ),
    updated_at = now()
from repair_jobs
where jobs.id = repair_jobs.id;

select public.prune_invalid_earnings_growth_metrics();

create index if not exists earnings_quarterly_financials_source_filing_idx
  on public.earnings_quarterly_financials (source_filing_id);
