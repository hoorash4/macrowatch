alter table public.us_market_stress_lead_weekly
  add column if not exists leverage_signal numeric(8, 2);

comment on column public.us_market_stress_lead_weekly.leverage_signal is
  'Normalized weekly NFCI nonfinancial leverage signal, displayed separately from MSI Lead for lead-time comparison.';
