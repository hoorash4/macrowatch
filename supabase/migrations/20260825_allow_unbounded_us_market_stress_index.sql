alter table public.us_market_stress_index_monthly
  drop constraint if exists us_market_stress_index_monthly_stress_index_check;

alter table public.us_market_stress_index_monthly
  add constraint us_market_stress_index_monthly_stress_index_check
  check (stress_index >= 0);
