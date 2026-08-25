alter table public.us_market_tension_weekly
  add column if not exists high_yield_oas_pct numeric(10, 4);

comment on column public.us_market_tension_weekly.high_yield_oas_pct is
  'ICE BofA U.S. High Yield Option-Adjusted Spread, recorded at the weekly observation date.';
