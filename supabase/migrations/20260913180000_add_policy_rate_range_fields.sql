alter table public.us_policy_rate_daily
  add column if not exists target_lower_pct numeric(8, 4),
  add column if not exists target_mid_pct numeric(8, 4);

comment on column public.us_policy_rate_daily.target_lower_pct is
  'Federal Reserve target-range lower bound from FRED DFEDTARL.';
comment on column public.us_policy_rate_daily.target_mid_pct is
  'Arithmetic midpoint of FRED DFEDTARL and DFEDTARU, used by the economic chart.';
comment on table public.us_policy_rate_daily is
  'Daily Federal Reserve target-range upper/lower/midpoint and optional U.S. 10-year Treasury yield.';
