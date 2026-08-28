create table if not exists public.equity_bond_source_monthly (
  series_code text not null check (
    series_code in ('SPY_ADJUSTED_CLOSE', 'TLT_ADJUSTED_CLOSE', 'T10Y2Y', 'BAA10Y')
  ),
  month date not null,
  observation_date date not null,
  value numeric(20, 8) not null,
  source text not null check (source in ('yahoo_finance', 'fred')),
  updated_at timestamptz not null default now(),
  primary key (series_code, month),
  check (month = date_trunc('month', month)::date),
  check (observation_date >= month and observation_date < month + interval '1 month')
);

comment on table public.equity_bond_source_monthly is
  'Canonical month-end observations that were not already retained elsewhere in MacroWatch. SPY and TLT are distribution-adjusted price proxies; T10Y2Y and BAA10Y are FRED series.';

create index if not exists equity_bond_source_monthly_month_idx
  on public.equity_bond_source_monthly (month desc);

alter table public.equity_bond_source_monthly enable row level security;

drop policy if exists "Authenticated users can read equity-bond source observations"
  on public.equity_bond_source_monthly;
create policy "Authenticated users can read equity-bond source observations"
  on public.equity_bond_source_monthly for select to authenticated using (true);

create table if not exists public.equity_bond_relative_forecasts (
  forecast_month date primary key,
  model_version text not null,
  source_through_date date not null,
  relative_momentum_6m numeric(14, 8) not null,
  real_yield_expanding_percentile numeric(10, 8) not null,
  yield_curve_10y_2y numeric(10, 6) not null,
  baa_spread_change_3m numeric(10, 6) not null,
  nfci_level numeric(12, 8) not null,
  stock_outperformance_probability numeric(10, 8) not null check (
    stock_outperformance_probability between 0 and 1
  ),
  bond_outperformance_probability numeric(10, 8) not null check (
    bond_outperformance_probability between 0 and 1
  ),
  expected_relative_return_pct numeric(14, 8) not null,
  downside_q25_relative_return_pct numeric(14, 8) not null,
  verdict text not null check (verdict in ('equity', 'long_treasury', 'neutral')),
  training_start_month date not null,
  training_end_month date not null,
  training_sample_count integer not null check (training_sample_count > 0),
  actual_relative_return_pct numeric(14, 8),
  outcome_status text not null default 'pending' check (outcome_status in ('pending', 'complete')),
  validation jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  check (forecast_month = date_trunc('month', forecast_month)::date),
  check (abs(stock_outperformance_probability + bond_outperformance_probability - 1) < 0.000001),
  check (
    (outcome_status = 'pending' and actual_relative_return_pct is null)
    or (outcome_status = 'complete' and actual_relative_return_pct is not null)
  )
);

comment on table public.equity_bond_relative_forecasts is
  'Purged walk-forward V1 forecasts of the following 12-month SPY adjusted total return minus TLT adjusted total return. Earnings breadth and FOMC remain explanatory and never overwrite the model result.';

create index if not exists equity_bond_relative_forecasts_month_idx
  on public.equity_bond_relative_forecasts (forecast_month desc);

alter table public.equity_bond_relative_forecasts enable row level security;

drop policy if exists "Authenticated users can read equity-bond forecasts"
  on public.equity_bond_relative_forecasts;
create policy "Authenticated users can read equity-bond forecasts"
  on public.equity_bond_relative_forecasts for select to authenticated using (true);
