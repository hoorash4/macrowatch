alter table public.us_market_tension_weekly
  add column if not exists financial_conditions_credit_index numeric(10, 4);

comment on column public.us_market_tension_weekly.financial_conditions_credit_index is
  'Chicago Fed NFCI credit subindex, recorded at the weekly observation date.';
