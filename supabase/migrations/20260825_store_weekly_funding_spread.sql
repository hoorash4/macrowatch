alter table public.us_market_tension_weekly
  add column if not exists short_term_funding_spread numeric(10, 4);

comment on column public.us_market_tension_weekly.short_term_funding_spread is
  'Commercial paper minus three-month Treasury bill spread, recorded at the weekly observation date.';
