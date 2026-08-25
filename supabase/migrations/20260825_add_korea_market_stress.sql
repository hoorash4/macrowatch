create table if not exists public.korea_market_stress_monthly (
  month date primary key,
  stress_index numeric(12, 2) not null,
  corporate_credit_spread numeric(12, 4),
  short_term_funding_spread numeric(12, 4),
  kospi_close numeric(14, 2),
  bok_fsi numeric(12, 2),
  is_provisional boolean not null default false,
  updated_at timestamptz not null default now()
);

create index if not exists korea_market_stress_monthly_month_idx on public.korea_market_stress_monthly (month desc);
alter table public.korea_market_stress_monthly enable row level security;
drop policy if exists "Authenticated users can read Korean market stress" on public.korea_market_stress_monthly;
create policy "Authenticated users can read Korean market stress" on public.korea_market_stress_monthly for select to authenticated using (true);

comment on table public.korea_market_stress_monthly is 'MacroWatch Korean market stress index. BOK FSI is a comparison series only, never an input.';
