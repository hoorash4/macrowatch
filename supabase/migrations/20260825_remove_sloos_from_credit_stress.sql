-- SLOOS is no longer part of US-MSI because quarterly releases distorted the monthly series.
alter table public.us_credit_stress_monthly
  drop column if exists sloos_tightening_pct;
