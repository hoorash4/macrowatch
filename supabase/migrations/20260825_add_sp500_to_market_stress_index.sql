alter table public.us_market_stress_index_monthly
  add column if not exists sp500_month_end_close numeric(12, 2);

comment on column public.us_market_stress_index_monthly.sp500_month_end_close is
  'S&P 500 final available daily close for the calendar month; comparison only, not an MSI component.';
