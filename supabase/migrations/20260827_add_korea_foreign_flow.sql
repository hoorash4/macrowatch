create table if not exists public.korea_foreign_flow_daily (
  observation_date date primary key,
  foreign_net_buy_amount numeric(22, 2) not null,
  kospi_trading_value numeric(22, 2) not null,
  foreign_flow_ratio numeric(16, 10) not null,
  usdkrw_rate numeric(12, 4) not null,
  usdkrw_return numeric(16, 10) not null,
  foreign_flow_z numeric(12, 6) not null,
  won_strength_z numeric(12, 6) not null,
  flow_index numeric(12, 6) not null,
  updated_at timestamptz not null default now()
);
create table if not exists public.korea_foreign_flow_raw (
  observation_date date primary key,
  foreign_net_buy_amount numeric(22, 2) not null,
  kospi_trading_value numeric(22, 2) not null,
  usdkrw_rate numeric(12, 4) not null,
  updated_at timestamptz not null default now()
);
alter table public.korea_foreign_flow_raw enable row level security;
alter table public.korea_foreign_flow_daily enable row level security;
drop policy if exists "Authenticated users can read Korea foreign flow" on public.korea_foreign_flow_daily;
create policy "Authenticated users can read Korea foreign flow" on public.korea_foreign_flow_daily for select to authenticated using (true);
comment on table public.korea_foreign_flow_daily is 'Daily zero-centered Korean foreign-capital flow intensity; equal-weighted causal z-scores of KOSPI foreign net-buy/trading-value ratio and KRW strength.';
