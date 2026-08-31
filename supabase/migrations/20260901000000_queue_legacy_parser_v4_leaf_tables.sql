-- One-time repair queue. Do not replay this migration on routine deploys.
-- Parser v4 ignores layout tables that wrap multiple real statements, which
-- prevents consolidated, separate and comparison-period rows from mixing.
with latest_filings as (
  select distinct on (filings.company_id, filings.fiscal_year, filings.source_report_code)
    filings.company_id,
    filings.fiscal_year,
    filings.source_report_code,
    filings.source_filing_id,
    filings.filing_date,
    filings.is_correction,
    filings.metadata->>'report_name' as report_name
  from public.earnings_filings filings
  where filings.source = 'open_dart'
    and filings.fiscal_year between 2002 and 2015
    and filings.source_report_code in ('11013', '11012', '11014', '11011')
    and filings.source_filing_id ~ '^\d{14}$'
  order by filings.company_id, filings.fiscal_year, filings.source_report_code,
           filings.is_correction desc, filings.filing_date desc, filings.updated_at desc
)
insert into public.earnings_ingestion_jobs (
  source, job_kind, company_id, business_year, report_code,
  reason, priority, metadata, collector_variant
)
select
  'open_dart', 'financial_period', latest.company_id, latest.fiscal_year,
  latest.source_report_code, 'repair', 10,
  jsonb_strip_nulls(jsonb_build_object(
    'receipt_no', latest.source_filing_id,
    'filed_on', latest.filing_date,
    'report_name', latest.report_name,
    'is_correction', latest.is_correction,
    'legacy_archive', true,
    'legacy_full_history_parser_version', 4,
    'repair_reason', 'ignore_nested_layout_tables_full_history'
  )),
  'legacy_archive'
from latest_filings latest
where not exists (
  select 1
  from public.earnings_ingestion_jobs outstanding
  where outstanding.source = 'open_dart'
    and outstanding.job_kind = 'financial_period'
    and outstanding.company_id = latest.company_id
    and outstanding.business_year = latest.fiscal_year
    and outstanding.report_code = latest.source_report_code
    and outstanding.status in ('pending', 'running', 'retry')
)
on conflict do nothing;
