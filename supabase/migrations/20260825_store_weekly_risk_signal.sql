alter table public.us_market_tension_weekly
  add column if not exists financial_conditions_risk_index numeric(10, 4);

comment on column public.us_market_tension_weekly.financial_conditions_risk_index is
  'Chicago Fed NFCI risk subindex, recorded at the weekly observation date.';
