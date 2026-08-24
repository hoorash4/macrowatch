create table if not exists public.us_market_stress_lead_weekly (
  week date primary key,
  lead_index numeric(8, 2) not null,
  lead_momentum numeric(8, 2),
  updated_at timestamptz not null default now()
);
alter table public.us_market_stress_lead_weekly enable row level security;
create policy "Authenticated users can read weekly U.S. market stress lead"
  on public.us_market_stress_lead_weekly for select to authenticated using (true);
