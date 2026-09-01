-- Revenue derivatives are reproducible and no longer part of Earnings
-- Momentum. Remove them so profit-only records do not need placeholder states.
alter table public.earnings_quarterly_growth_metrics
  drop column if exists revenue_yoy_pct,
  drop column if exists revenue_yoy_state,
  drop column if exists revenue_yoy_delta_pp,
  drop column if exists revenue_qoq_raw_pct,
  drop column if exists revenue_qoq_state,
  drop column if exists revenue_qoq_seasonal_baseline_pct,
  drop column if exists revenue_qoq_seasonally_adjusted_pct,
  drop column if exists revenue_qoq_seasonally_adjusted_delta_pp,
  drop column if exists revenue_qoq_seasonal_sample_count;

-- Market rows are also derived from canonical quarters and can be rebuilt.
delete from public.earnings_market_quarterly_metrics
where metric = 'revenue';
