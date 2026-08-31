-- The quarantine migration can meet an existing historical job identity. In
-- that case `on conflict do nothing` preserves the job but cannot attach the
-- repair marker. Recover the exact affected company-years from the canonical
-- quarantined rows instead of relying on mutable job metadata.

with affected_years as (
  select distinct financials.company_id, financials.fiscal_year
  from public.earnings_quarterly_financials financials
  join public.earnings_filings filings
    on filings.id = financials.source_filing_id
  where financials.quality_status = 'review_required'
    and financials.revenue is null
    and financials.operating_income is null
    and financials.net_income is null
    and financials.missing_metrics @> array['revenue', 'operating_income', 'net_income']::text[]
    and filings.source = 'open_dart'
    and filings.metadata->>'financial_method' = 'legacy_dart_document_archive_v1'
)
update public.earnings_ingestion_jobs jobs
set status = 'retry',
    attempts = 0,
    available_at = now(),
    claimed_at = null,
    completed_at = null,
    last_error = null,
    metadata = jobs.metadata || jsonb_build_object(
      'backfill', true,
      'legacy_archive', true,
      'repair_reason', 'legacy_zero_quarter_quarantine'
    ),
    collector_variant = 'legacy_archive',
    updated_at = now()
from affected_years affected
where jobs.source = 'open_dart'
  and jobs.job_kind = 'financial_period'
  and jobs.company_id = affected.company_id
  and jobs.business_year = affected.fiscal_year
  and jobs.report_code in ('11013', '11012', '11014', '11011')
  and coalesce(jobs.metadata->>'receipt_no', '') <> ''
  and jobs.status in ('failed', 'retry')
  and jobs.attempts >= jobs.max_attempts;
