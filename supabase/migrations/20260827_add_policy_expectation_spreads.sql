create table if not exists public.policy_expectation_spreads (
  observation_date date primary key,
  treasury_3m_rate numeric(9, 6) not null,
  treasury_2y_rate numeric(9, 6) not null,
  effr_rate numeric(9, 6) not null,
  near_term_spread_bps numeric(12, 6) not null,
  cycle_spread_bps numeric(12, 6) not null,
  expectation_spread_bps numeric(12, 6) not null,
  updated_at timestamptz not null default now()
);

alter table public.policy_expectation_spreads enable row level security;

drop policy if exists "Authenticated users can read policy expectation spreads"
  on public.policy_expectation_spreads;
create policy "Authenticated users can read policy expectation spreads"
  on public.policy_expectation_spreads for select to authenticated using (true);

comment on table public.policy_expectation_spreads is
  'Daily market-implied policy-rate expectation spread built from 70% three-month Treasury and 30% two-year Treasury spreads to EFFR.';
comment on column public.policy_expectation_spreads.expectation_spread_bps is
  '0.7 * (DGS3MO - DFF) + 0.3 * (DGS2 - DFF), expressed in basis points.';
