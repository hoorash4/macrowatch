alter table public.us_market_tension_weekly
  add column if not exists sp500_friday_close numeric(12, 2);

comment on column public.us_market_tension_weekly.sp500_friday_close is
  'S&P 500 final available close for each Friday-ended week.';
