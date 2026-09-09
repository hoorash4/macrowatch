create table if not exists public.kr_small_business_risk_monthly (
  month date primary key,
  risk_index numeric(8, 2) not null,
  funding_outlook_sbhi numeric(8, 4) not null,
  funding_source_month date not null,
  utilization_sa_pct numeric(8, 4) not null,
  utilization_source_month date not null,
  sme_corporation_delinquency_pct numeric(8, 4) not null,
  delinquency_source_month date not null,
  headline_outlook_sbhi numeric(8, 4),
  is_provisional boolean not null default true,
  method_version text not null default 'kr-sme-risk-v1',
  updated_at timestamptz not null default now()
);

create index if not exists kr_small_business_risk_month_idx
  on public.kr_small_business_risk_monthly (month desc);

alter table public.kr_small_business_risk_monthly enable row level security;

drop policy if exists "Authenticated users can read Korean small business risk" on public.kr_small_business_risk_monthly;
create policy "Authenticated users can read Korean small business risk"
  on public.kr_small_business_risk_monthly for select to authenticated using (true);

comment on table public.kr_small_business_risk_monthly is
  'Monthly Korean SME risk index: funding outlook SBHI 35%, seasonally adjusted manufacturing utilization 30%, and SME corporation delinquency rate 35%.';
