-- Publish only the derived US-MSI value. Component observations and weights
-- remain in the server-only source table.
create table if not exists public.us_market_stress_index_monthly (
  month date primary key,
  stress_index numeric(6, 2) not null check (stress_index >= 0 and stress_index <= 100),
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now(),
  check (month = date_trunc('month', month)::date)
);

create index if not exists us_market_stress_index_monthly_month_idx
  on public.us_market_stress_index_monthly (month desc);

alter table public.us_market_stress_index_monthly enable row level security;

drop policy if exists "Authenticated users can read U.S. market stress index" on public.us_market_stress_index_monthly;
create policy "Authenticated users can read U.S. market stress index"
  on public.us_market_stress_index_monthly for select to authenticated using (true);

-- The raw components are an internal research asset and must not be queryable
-- from the browser after the public index is introduced.
drop policy if exists "Authenticated users can read U.S. credit stress" on public.us_credit_stress_monthly;

comment on table public.us_market_stress_index_monthly is
  'Published monthly U.S. Market Stress Index. Raw input observations and calculation details are intentionally not exposed.';
