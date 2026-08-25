alter table public.em_market_stress_weekly
  add column if not exists eem_weekly_close numeric(12, 2);

comment on column public.em_market_stress_weekly.eem_weekly_close is
  'EEM weekly closing price for personal-use comparison only.';
