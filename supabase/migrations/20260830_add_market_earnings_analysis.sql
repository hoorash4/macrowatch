-- Compact server-calculated market earnings outputs. Canonical company
-- financials remain the only raw source; browser clients read these small
-- derivative tables and never download every constituent quarter.
create table if not exists public.earnings_market_quarterly_metrics (
  index_id text not null references public.earnings_indices(index_id) on delete restrict,
  fiscal_year integer not null check (fiscal_year between 1900 and 2200),
  fiscal_quarter smallint not null check (fiscal_quarter between 1 and 4),
  metric text not null check (metric in ('revenue', 'operating_income', 'net_income')),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  universe_basis text not null,
  universe_company_count smallint not null check (universe_company_count > 0),
  comparable_company_count smallint not null check (comparable_company_count >= 0),
  delta_comparable_company_count smallint not null check (delta_comparable_company_count >= 0),
  company_coverage_pct numeric(20, 8) not null,
  current_total numeric(38, 8),
  prior_total numeric(38, 8),
  yoy_pct numeric(20, 8),
  yoy_state text not null check (yoy_state in (
    'normal', 'black_turn', 'red_turn', 'loss_narrowing',
    'loss_widening', 'loss_unchanged', 'from_zero'
  )),
  previous_yoy_pct_common numeric(20, 8),
  yoy_delta_pp numeric(20, 8),
  is_provisional boolean not null,
  calculation_version integer not null check (calculation_version > 0),
  calculated_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_id, fiscal_year, fiscal_quarter, metric),
  check (yoy_state = 'normal' or yoy_delta_pp is null)
);

create index if not exists earnings_market_metrics_period_idx
  on public.earnings_market_quarterly_metrics
  (index_id, metric, fiscal_year desc, fiscal_quarter desc);

create table if not exists public.earnings_market_quarterly_breadth (
  index_id text not null references public.earnings_indices(index_id) on delete restrict,
  market_year integer not null check (market_year between 1900 and 2200),
  market_quarter smallint not null check (market_quarter between 1 and 4),
  universe_basis text not null,
  universe_company_count smallint not null check (universe_company_count > 0),
  comparable_company_count smallint not null check (comparable_company_count >= 0),
  is_provisional boolean not null,
  company_coverage_pct numeric(20, 8) not null,
  op_coverage_pct numeric(20, 8),
  current_total_op numeric(38, 8),
  prior_total_op numeric(38, 8),
  net_op_change numeric(38, 8),
  op_growth_pct numeric(20, 8),
  aggregate_turn text not null check (aggregate_turn in ('none', 'black_turn', 'red_turn', 'unavailable')),
  positive_company_count smallint not null check (positive_company_count >= 0),
  negative_company_count smallint not null check (negative_company_count >= 0),
  unchanged_company_count smallint not null check (unchanged_company_count >= 0),
  earnings_breadth_pct numeric(20, 8),
  breadth_delta_pp numeric(20, 8),
  breadth_delta_comparable_count smallint not null check (breadth_delta_comparable_count >= 0),
  breadth_delta_company_coverage_pct numeric(20, 8) not null,
  breadth_delta_op_coverage_pct numeric(20, 8),
  positive_contribution_total numeric(38, 8) not null,
  negative_contribution_total_abs numeric(38, 8) not null,
  top5_positive_contribution_share_pct numeric(20, 8),
  top5_negative_contribution_share_pct numeric(20, 8),
  negative_offset_ratio_pct numeric(20, 8),
  black_turn_count smallint not null check (black_turn_count >= 0),
  red_turn_count smallint not null check (red_turn_count >= 0),
  profit_turn_net smallint not null,
  classification text not null,
  calculation_version integer not null check (calculation_version > 0),
  calculated_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_id, market_year, market_quarter)
);

create index if not exists earnings_market_breadth_period_idx
  on public.earnings_market_quarterly_breadth
  (index_id, market_year desc, market_quarter desc);

alter table public.earnings_market_quarterly_metrics enable row level security;
alter table public.earnings_market_quarterly_breadth enable row level security;

drop policy if exists "Authenticated users can read market earnings metrics"
  on public.earnings_market_quarterly_metrics;
create policy "Authenticated users can read market earnings metrics"
  on public.earnings_market_quarterly_metrics for select to authenticated using (true);

drop policy if exists "Authenticated users can read market earnings breadth"
  on public.earnings_market_quarterly_breadth;
create policy "Authenticated users can read market earnings breadth"
  on public.earnings_market_quarterly_breadth for select to authenticated using (true);

grant select on public.earnings_market_quarterly_metrics to authenticated;
grant select on public.earnings_market_quarterly_breadth to authenticated;

-- V2 expresses disparity as the finite point distance between two rebased
-- lines. Renaming preserves existing rows until the worker recalculates them.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'earnings_company_price_gaps'
      and column_name = 'gap_pct'
  ) and not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'earnings_company_price_gaps'
      and column_name = 'gap_points'
  ) then
    alter table public.earnings_company_price_gaps rename column gap_pct to gap_points;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'earnings_company_price_gaps'
      and column_name = 'gap_delta_pp'
  ) and not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'earnings_company_price_gaps'
      and column_name = 'gap_delta_points'
  ) then
    alter table public.earnings_company_price_gaps rename column gap_delta_pp to gap_delta_points;
  end if;
end
$$;

comment on table public.earnings_market_quarterly_metrics is
  'Server-calculated signed revenue, operating-income and net-income market aggregates with valid positive-baseline YoY and common-cohort YoY delta.';
comment on table public.earnings_market_quarterly_breadth is
  'Operating-income breadth, concentration, negative offset and turnaround counts calculated from canonical quarters.';
comment on table public.earnings_company_price_gaps is
  'Quarterly rebased TTM operating-income and adjusted-price lines with finite index-point distance and quarter-over-quarter distance delta.';
