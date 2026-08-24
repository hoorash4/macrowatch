-- One latest, provisional observation for the faster-moving credit components.
-- Monthly history remains unchanged and the slower bankruptcy series is omitted.
create table if not exists public.us_credit_stress_latest (
  singleton boolean primary key default true check (singleton),
  as_of date not null,
  high_yield_oas_pct numeric(8, 4),
  financial_conditions_credit_index numeric(10, 6),
  updated_at timestamptz not null default now()
);

alter table public.us_credit_stress_latest enable row level security;

create policy "Authenticated users can read latest U.S. credit stress"
  on public.us_credit_stress_latest for select to authenticated using (true);

comment on table public.us_credit_stress_latest is
  'Latest provisional high-yield and credit-conditions observations. Bankruptcy filings are intentionally excluded until their scheduled release.';
