create table if not exists public.us_small_business_risk_monthly (
  month date primary key,
  risk_index numeric(8, 2) not null,
  sales_expectation_net numeric(8, 4) not null,
  borrowing_difficulty_pct numeric(8, 4) not null,
  high_yield_oas_pct numeric(8, 4),
  includes_oas boolean not null default false,
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists us_small_business_risk_month_idx
  on public.us_small_business_risk_monthly (month desc);

alter table public.us_small_business_risk_monthly enable row level security;

drop policy if exists "Authenticated users can read U.S. small business risk" on public.us_small_business_risk_monthly;
create policy "Authenticated users can read U.S. small business risk"
  on public.us_small_business_risk_monthly for select to authenticated using (true);

comment on table public.us_small_business_risk_monthly is
  'Monthly U.S. small-business risk index: NFIB sales expectations 30, borrowing difficulty 40, and HY OAS 30 when available.';
