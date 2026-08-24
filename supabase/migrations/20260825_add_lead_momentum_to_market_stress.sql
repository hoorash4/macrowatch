alter table public.us_market_stress_index_monthly
  add column if not exists lead_momentum numeric(8, 2);

comment on column public.us_market_stress_index_monthly.lead_momentum is
  'Equal-weight normalized month-over-month change across the three MSI Lead components; not an MSI component.';
