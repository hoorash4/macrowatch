alter table public.us_market_stress_index_monthly
  add column if not exists lead_index numeric(8, 2);

comment on column public.us_market_stress_index_monthly.lead_index is
  'Forward-looking market-stress signal derived from credit deterioration speed and short-term funding spread; not an MSI component.';
