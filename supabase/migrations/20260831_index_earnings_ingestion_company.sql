-- Company-year grouping is the hot path for the resumable legacy DART worker,
-- and company_id is also the referencing side of the queue's foreign key.
create index if not exists earnings_ingestion_jobs_company_id_idx
  on public.earnings_ingestion_jobs (company_id, business_year, report_code);
