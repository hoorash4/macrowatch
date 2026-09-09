create table if not exists public.us_policy_rate_daily (
  observed_on date primary key,
  target_upper_pct numeric(8, 4) not null,
  source text not null default 'FRED:DFEDTARU',
  updated_at timestamptz not null default now()
);

create index if not exists us_policy_rate_daily_observed_idx
  on public.us_policy_rate_daily (observed_on desc);

alter table public.us_policy_rate_daily enable row level security;
drop policy if exists "Authenticated users can read U.S. policy rate daily" on public.us_policy_rate_daily;
create policy "Authenticated users can read U.S. policy rate daily"
  on public.us_policy_rate_daily for select to authenticated using (true);

grant select on table public.us_policy_rate_daily to authenticated;
grant select, insert, update, delete on table public.us_policy_rate_daily to service_role;

comment on table public.us_policy_rate_daily is
  'Daily Federal Reserve target-range upper bound from FRED DFEDTARU, stored independently from inflation forecasts.';

drop table if exists public.us_inflation_leading_daily;
