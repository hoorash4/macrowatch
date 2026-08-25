-- Separate the fast market-tension series from the slower monthly stress series
-- without altering historical lead data. The weekly index uses only daily and
-- weekly inputs; the monthly index uses only monthly or quarterly-released inputs.

create table if not exists public.us_market_tension_weekly (
  week date primary key,
  tension_index numeric(8, 2) not null,
  tension_momentum numeric(8, 2),
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists us_market_tension_weekly_week_idx
  on public.us_market_tension_weekly (week desc);

alter table public.us_market_tension_weekly enable row level security;

drop policy if exists "Authenticated users can read weekly U.S. market tension" on public.us_market_tension_weekly;
create policy "Authenticated users can read weekly U.S. market tension"
  on public.us_market_tension_weekly for select to authenticated using (true);

comment on table public.us_market_tension_weekly is
  'Published weekly U.S. Market Tension Index. It uses only daily and weekly inputs.';
