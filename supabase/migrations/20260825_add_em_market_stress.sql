create table if not exists public.em_market_stress_weekly (
  week date primary key,
  stress_index numeric(12, 2) not null check (stress_index >= 0),
  high_yield_4w_average numeric(12, 4) not null,
  tail_risk_4w_average numeric(12, 4) not null,
  blended_4w_average numeric(12, 4) not null,
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists em_market_stress_weekly_week_idx
  on public.em_market_stress_weekly (week desc);

alter table public.em_market_stress_weekly enable row level security;

drop policy if exists "Authenticated users can read EM market stress" on public.em_market_stress_weekly;
create policy "Authenticated users can read EM market stress"
  on public.em_market_stress_weekly for select to authenticated using (true);

comment on table public.em_market_stress_weekly is
  'Published weekly Emerging Market Stress Index. Raw source observations and component weights remain server-only.';
