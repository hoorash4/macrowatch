-- Legacy DART HTML can repeat current-quarter and cumulative columns. Earlier
-- parser versions accepted the subtraction result even when standalone revenue
-- became zero, which polluted market averages and their growth rates.
--
-- Preserve filings and canonical row identity for auditability, quarantine only
-- affected values, and requeue the full company-year so cumulative subtraction
-- always has all four reports available. Derived Korean market tables are
-- replaceable and are rebuilt automatically after this migration deploys.

create temporary table legacy_dart_zero_quarter_quarantine as
select financials.company_id, financials.fiscal_year,
  financials.fiscal_quarter
from public.earnings_quarterly_financials financials
join public.earnings_filings filings on filings.id = financials.source_filing_id
where financials.revenue = 0
  and filings.source = 'open_dart'
  and filings.metadata->>'financial_method' = 'legacy_dart_document_archive_v1';

create index legacy_dart_zero_quarter_quarantine_company_year_idx
  on legacy_dart_zero_quarter_quarantine (company_id, fiscal_year);

with affected_years as (
  select distinct company_id, fiscal_year as business_year
  from legacy_dart_zero_quarter_quarantine
), source_jobs as (
  select distinct on (jobs.company_id, jobs.business_year, jobs.report_code)
    jobs.company_id, jobs.business_year, jobs.report_code,
    (jobs.metadata - 'outcome') || jsonb_build_object(
      'backfill', true,
      'legacy_archive', true,
      'repair_reason', 'legacy_zero_quarter_quarantine'
    ) as metadata
  from public.earnings_ingestion_jobs jobs
  join affected_years affected
    on affected.company_id = jobs.company_id
   and affected.business_year = jobs.business_year
  where jobs.source = 'open_dart'
    and jobs.job_kind = 'financial_period'
    and jobs.report_code in ('11013', '11012', '11014', '11011')
    and coalesce(jobs.metadata->>'receipt_no', '') <> ''
  order by jobs.company_id, jobs.business_year, jobs.report_code, jobs.id desc
)
insert into public.earnings_ingestion_jobs (
  source, job_kind, company_id, business_year, report_code, reason,
  priority, metadata, collector_variant
)
select 'open_dart', 'financial_period', source_jobs.company_id,
  source_jobs.business_year, source_jobs.report_code, 'repair',
  10, source_jobs.metadata, 'legacy_archive'
from source_jobs
on conflict do nothing;

update public.earnings_quarterly_financials financials
set revenue = null,
    operating_income = null,
    net_income = null,
    quality_status = 'review_required',
    missing_metrics = array['revenue', 'operating_income', 'net_income']::text[],
    canonical_version = financials.canonical_version + 1,
    calculated_at = now(),
    updated_at = now()
from legacy_dart_zero_quarter_quarantine quarantine
where quarantine.company_id = financials.company_id
  and quarantine.fiscal_year = financials.fiscal_year
  and quarantine.fiscal_quarter = financials.fiscal_quarter;

delete from public.earnings_market_quarterly_metrics
where index_id in ('KOSPI100', 'KOSDAQ50')
  and exists (select 1 from legacy_dart_zero_quarter_quarantine);

delete from public.earnings_market_quarterly_breadth
where index_id in ('KOSPI100', 'KOSDAQ50')
  and exists (select 1 from legacy_dart_zero_quarter_quarantine);
