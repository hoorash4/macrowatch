-- Monthly U.S. credit-stress indicators. Only derived numeric observations are
-- retained; source spreadsheets and daily/weekly source rows are not stored.
create table if not exists public.us_credit_stress_monthly (
  month date primary key,
  high_yield_oas_pct numeric(8, 4),
  financial_conditions_credit_index numeric(10, 6),
  business_bankruptcy_filings integer check (business_bankruptcy_filings >= 0),
  updated_at timestamptz not null default now(),
  check (month = date_trunc('month', month)::date)
);

create index if not exists us_credit_stress_monthly_month_idx
  on public.us_credit_stress_monthly (month desc);

alter table public.us_credit_stress_monthly enable row level security;

drop policy if exists "Authenticated users can read U.S. credit stress" on public.us_credit_stress_monthly;
create policy "Authenticated users can read U.S. credit stress"
  on public.us_credit_stress_monthly for select to authenticated using (true);

comment on table public.us_credit_stress_monthly is
  'Monthly derived values: FRED high-yield OAS, Chicago Fed credit conditions, and U.S. Courts business bankruptcy filings. Source files are not retained.';
