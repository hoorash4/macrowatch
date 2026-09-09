-- Public chart output for the fixed MacroWatch U.S. inflation model.  Source
-- observations and fitted coefficients remain in the collector, outside the
-- browser-readable schema.
create table if not exists public.us_inflation_monthly (
  month date primary key,
  headline_yoy_pct numeric(8, 4) not null,
  core_yoy_pct numeric(8, 4) not null,
  policy_rate_upper_pct numeric(8, 4),
  headline_real_rate_pct numeric(8, 4),
  core_real_rate_pct numeric(8, 4),
  status text not null check (status in ('final', 'provisional')),
  model_version text not null,
  data_as_of date not null,
  updated_at timestamptz not null default now(),
  check (month = date_trunc('month', month)::date)
);

create index if not exists us_inflation_monthly_month_idx
  on public.us_inflation_monthly (month desc);

alter table public.us_inflation_monthly enable row level security;
drop policy if exists "Authenticated users can read U.S. inflation monthly" on public.us_inflation_monthly;
create policy "Authenticated users can read U.S. inflation monthly"
  on public.us_inflation_monthly for select to authenticated using (true);

grant select on table public.us_inflation_monthly to authenticated;
grant select, insert, update, delete on table public.us_inflation_monthly to service_role;

create table if not exists public.us_inflation_leading_daily (
  observed_on date primary key,
  target_month date not null,
  headline_leading_yoy_pct numeric(8, 4) not null,
  core_leading_yoy_pct numeric(8, 4) not null,
  policy_rate_upper_pct numeric(8, 4),
  headline_real_rate_pct numeric(8, 4),
  core_real_rate_pct numeric(8, 4),
  model_version text not null,
  updated_at timestamptz not null default now(),
  check (target_month = date_trunc('month', target_month)::date)
);

create index if not exists us_inflation_leading_daily_observed_idx
  on public.us_inflation_leading_daily (observed_on desc);

alter table public.us_inflation_leading_daily enable row level security;
drop policy if exists "Authenticated users can read U.S. inflation leading daily" on public.us_inflation_leading_daily;
create policy "Authenticated users can read U.S. inflation leading daily"
  on public.us_inflation_leading_daily for select to authenticated using (true);

grant select on table public.us_inflation_leading_daily to authenticated;
grant select, insert, update, delete on table public.us_inflation_leading_daily to service_role;

comment on table public.us_inflation_monthly is
  'Monthly final/provisional headline and core inflation composites: PCE 60%, shelter-adjusted CPI 30%, consumer PPI 10%.';
comment on table public.us_inflation_leading_daily is
  'Daily MacroWatch leading inflation estimates and exact Fisher real rates. Core uses the approved headline residual correction; used-vehicle correction is excluded.';
