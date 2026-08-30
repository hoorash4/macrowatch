comment on column public.earnings_market_quarterly_metrics.universe_basis is
  'point_in_time_market_cap_snapshot when both comparison rankings are known; oldest_available_universe_average_fallback when historical ranking reconstruction is unavailable; point_in_time_with_fallback_average_comparison for a mixed YoY pair.';

comment on column public.earnings_market_quarterly_metrics.current_average is
  'Simple average of only the companies with reported values. Missing companies are never imputed or scaled to the target universe count.';

comment on column public.earnings_market_quarterly_metrics.prior_average is
  'Simple year-ago average of only the companies with reported values. Missing companies are never imputed.';

comment on column public.earnings_market_quarterly_metrics.comparable_company_count is
  'Number of companies whose actual current-quarter value is included in current_average.';

comment on column public.earnings_market_quarterly_metrics.delta_comparable_company_count is
  'Number of companies whose actual year-ago value is included in prior_average.';
