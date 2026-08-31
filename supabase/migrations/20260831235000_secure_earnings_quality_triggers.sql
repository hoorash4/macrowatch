-- Trigger functions are internal database hooks, not public RPC endpoints.
revoke all on function public.apply_earnings_job_review_outcome()
  from public, anon, authenticated;
revoke all on function public.remove_invalid_earnings_growth_metric()
  from public, anon, authenticated;

-- Quality audits and canonical replacement join through this foreign key.
create index if not exists earnings_quarterly_financials_source_filing_idx
  on public.earnings_quarterly_financials (source_filing_id);
