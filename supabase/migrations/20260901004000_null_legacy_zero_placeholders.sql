-- Historical parser failures were quarantined as review_required, but a few
-- rows still carried impossible negative revenue or 0/0/0 placeholders.
-- Preserve the quarter/source identity while representing unavailable facts
-- as NULL so no downstream consumer can mistake invalid parsing for results.
update public.earnings_quarterly_financials
set revenue = null,
    operating_income = null,
    net_income = null,
    missing_metrics = array['revenue', 'operating_income', 'net_income']::text[],
    quality_status = 'review_required',
    updated_at = now()
where quality_status = 'review_required'
  and (
    revenue < 0
    or (revenue = 0 and operating_income = 0 and net_income = 0)
  );
