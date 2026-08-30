alter table public.earnings_market_quarterly_metrics
    add column if not exists current_average numeric,
    add column if not exists prior_average numeric;

comment on column public.earnings_market_quarterly_metrics.current_average is
    'Per-company average for the current quarter: current_total / reported company count.';

comment on column public.earnings_market_quarterly_metrics.prior_average is
    'Per-company average for the year-ago comparison quarter: prior_total / reported company count.';

comment on column public.earnings_market_quarterly_metrics.yoy_pct is
    'YoY percentage change of per-company average, not raw aggregate total.';

comment on column public.earnings_market_quarterly_metrics.yoy_delta_pp is
    'Quarter-over-quarter change in average-based YoY percentage points.';
