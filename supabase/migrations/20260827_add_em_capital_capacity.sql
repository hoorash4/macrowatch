create table if not exists public.em_capital_capacity_daily (
  observation_date date primary key,
  em_dollar_index numeric(12, 6) not null,
  real_yield_10y numeric(9, 6) not null,
  us_high_yield_oas numeric(9, 6) not null,
  nfci numeric(12, 6) not null,
  capacity_index numeric(12, 6) not null,
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.em_capital_capacity_daily
  add column if not exists is_provisional boolean not null default false;

alter table public.em_capital_capacity_daily enable row level security;

drop policy if exists "Authenticated users can read EM capital capacity"
  on public.em_capital_capacity_daily;
create policy "Authenticated users can read EM capital capacity"
  on public.em_capital_capacity_daily for select to authenticated using (true);

comment on table public.em_capital_capacity_daily is
  'Daily zero-centered capacity for U.S. capital to move toward emerging markets; equal-weighted causal z-scores of EM dollar, 10Y real yield, US HY OAS, and NFCI.';
