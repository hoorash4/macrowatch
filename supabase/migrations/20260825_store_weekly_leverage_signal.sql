alter table public.us_market_tension_weekly
  add column if not exists nonfinancial_leverage_index numeric(10, 4);

comment on column public.us_market_tension_weekly.nonfinancial_leverage_index is
  'Chicago Fed NFCI nonfinancial leverage subindex, recorded at the weekly observation date.';
