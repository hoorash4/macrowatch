-- Re-run every legacy Q1 after parser v14 separated Q1's one-value-per-
-- period layout from Q2/Q3 standalone-and-cumulative pairs.  Some Q1 tables
-- show the 3-month/YTD header but publish one identical period value only;
-- treating the second numeric cell as current silently selected prior-year Q1.
with latest_filings as (
  select distinct on (filings.company_id, filings.fiscal_year, filings.source_report_code)
    filings.company_id, filings.fiscal_year, filings.source_report_code,
    filings.source_filing_id, filings.filing_date, filings.is_correction,
    filings.metadata->>'report_name' as report_name
  from public.earnings_filings filings
  where filings.source = 'open_dart'
    and filings.fiscal_year between 2002 and 2015
    and filings.source_report_code = '11013'
    and filings.source_filing_id ~ '^\d{14}$'
  order by filings.company_id, filings.fiscal_year, filings.source_report_code,
           filings.is_correction desc, filings.filing_date desc, filings.updated_at desc
)
insert into public.earnings_ingestion_jobs (
  source, job_kind, company_id, business_year, report_code,
  reason, priority, metadata, collector_variant
)
select 'open_dart', 'financial_period', latest.company_id, latest.fiscal_year,
       latest.source_report_code, 'repair', 10,
       jsonb_strip_nulls(jsonb_build_object(
         'receipt_no', latest.source_filing_id,
         'filed_on', latest.filing_date,
         'report_name', latest.report_name,
         'is_correction', latest.is_correction,
         'legacy_archive', true,
         'legacy_full_history_parser_version', 14,
         'repair_reason', 'select_first_current_period_for_q1'
       )),
       'legacy_archive'
from latest_filings latest
where not exists (
  select 1 from public.earnings_ingestion_jobs outstanding
  where outstanding.source = 'open_dart'
    and outstanding.job_kind = 'financial_period'
    and outstanding.company_id = latest.company_id
    and outstanding.business_year = latest.fiscal_year
    and outstanding.report_code = latest.source_report_code
    and outstanding.status in ('pending', 'running', 'retry')
)
on conflict do nothing;
