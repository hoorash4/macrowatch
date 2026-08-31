-- Re-run unresolved legacy interim filings after parser v16 learned to strip
-- long basic/diluted-EPS disclosures appended to the net-income account label.
-- Values still come only from the published income-statement cells.
with targets as (
  select distinct on (
    financials.company_id, financials.fiscal_year, financials.fiscal_quarter
  )
    financials.company_id,
    financials.fiscal_year,
    filings.source_report_code,
    filings.source_filing_id,
    filings.filing_date,
    filings.is_correction,
    filings.metadata->>'report_name' as report_name
  from public.earnings_quarterly_financials financials
  join public.earnings_filings filings on filings.id = financials.source_filing_id
  where financials.quality_status = 'review_required'
    and financials.revenue is null
    and financials.operating_income is null
    and financials.net_income is null
    and filings.source = 'open_dart'
    and filings.fiscal_year between 2002 and 2015
    and filings.source_report_code in ('11012', '11014')
    and filings.source_filing_id ~ '^\d{14}$'
  order by financials.company_id, financials.fiscal_year,
           financials.fiscal_quarter, filings.is_correction desc,
           filings.filing_date desc, filings.updated_at desc
)
insert into public.earnings_ingestion_jobs (
  source, job_kind, company_id, business_year, report_code,
  reason, priority, metadata, collector_variant
)
select
  'open_dart', 'financial_period', targets.company_id, targets.fiscal_year,
  targets.source_report_code, 'repair', 10,
  jsonb_strip_nulls(jsonb_build_object(
    'receipt_no', targets.source_filing_id,
    'filed_on', targets.filing_date,
    'report_name', targets.report_name,
    'is_correction', targets.is_correction,
    'legacy_archive', true,
    'legacy_full_history_parser_version', 16,
    'repair_reason', 'strip_eps_disclosure_from_net_income_label'
  )),
  'legacy_archive'
from targets
where not exists (
  select 1 from public.earnings_ingestion_jobs outstanding
  where outstanding.source = 'open_dart'
    and outstanding.job_kind = 'financial_period'
    and outstanding.company_id = targets.company_id
    and outstanding.business_year = targets.fiscal_year
    and outstanding.report_code = targets.source_report_code
    and outstanding.status in ('pending', 'running', 'retry')
)
on conflict do nothing;
