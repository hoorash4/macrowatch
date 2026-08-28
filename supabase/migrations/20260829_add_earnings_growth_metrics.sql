-- One compact, recalculable row per company-quarter. Raw financial values and
-- provider payloads remain in their canonical tables and are never duplicated.
create table if not exists public.earnings_quarterly_growth_metrics (
  company_id uuid not null,
  fiscal_year integer not null,
  fiscal_quarter smallint not null check (fiscal_quarter between 1 and 4),

  revenue_yoy_pct numeric(20, 8),
  revenue_yoy_state text not null,
  revenue_yoy_delta_pp numeric(20, 8),
  revenue_qoq_raw_pct numeric(20, 8),
  revenue_qoq_state text not null,
  revenue_qoq_seasonal_baseline_pct numeric(20, 8),
  revenue_qoq_seasonally_adjusted_pct numeric(20, 8),
  revenue_qoq_seasonally_adjusted_delta_pp numeric(20, 8),
  revenue_qoq_seasonal_sample_count smallint not null default 0,

  operating_income_yoy_pct numeric(20, 8),
  operating_income_yoy_state text not null,
  operating_income_yoy_delta_pp numeric(20, 8),
  operating_income_qoq_raw_pct numeric(20, 8),
  operating_income_qoq_state text not null,
  operating_income_qoq_seasonal_baseline_pct numeric(20, 8),
  operating_income_qoq_seasonally_adjusted_pct numeric(20, 8),
  operating_income_qoq_seasonally_adjusted_delta_pp numeric(20, 8),
  operating_income_qoq_seasonal_sample_count smallint not null default 0,

  net_income_yoy_pct numeric(20, 8),
  net_income_yoy_state text not null,
  net_income_yoy_delta_pp numeric(20, 8),
  net_income_qoq_raw_pct numeric(20, 8),
  net_income_qoq_state text not null,
  net_income_qoq_seasonal_baseline_pct numeric(20, 8),
  net_income_qoq_seasonally_adjusted_pct numeric(20, 8),
  net_income_qoq_seasonally_adjusted_delta_pp numeric(20, 8),
  net_income_qoq_seasonal_sample_count smallint not null default 0,

  source_canonical_version integer not null check (source_canonical_version > 0),
  calculation_version integer not null default 1 check (calculation_version > 0),
  calculated_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (company_id, fiscal_year, fiscal_quarter),
  foreign key (company_id, fiscal_year, fiscal_quarter)
    references public.earnings_quarterly_financials(company_id, fiscal_year, fiscal_quarter)
    on delete cascade,
  check (revenue_yoy_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (revenue_qoq_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (operating_income_yoy_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (operating_income_qoq_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (net_income_yoy_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (net_income_qoq_state in (
    'normal', 'black_turn', 'red_turn', 'negative_base', 'from_zero',
    'missing_prior', 'currency_mismatch', 'scope_mismatch'
  )),
  check (revenue_qoq_seasonal_sample_count between 0 and 5),
  check (operating_income_qoq_seasonal_sample_count between 0 and 5),
  check (net_income_qoq_seasonal_sample_count between 0 and 5)
);

comment on table public.earnings_quarterly_growth_metrics is
  'Compact recalculable YoY, YoY-delta and seasonally adjusted QoQ metrics; one row per canonical company-quarter and no raw-value duplication.';

alter table public.earnings_quarterly_growth_metrics enable row level security;

drop policy if exists "Authenticated users can read earnings growth metrics"
  on public.earnings_quarterly_growth_metrics;
create policy "Authenticated users can read earnings growth metrics"
  on public.earnings_quarterly_growth_metrics for select to authenticated using (true);

-- Keep mathematically incomparable turn/loss periods out of acceleration and
-- seasonal-adjustment outputs even if a future worker accidentally regresses.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'earnings_growth_revenue_state_values_ck'
      and conrelid = 'public.earnings_quarterly_growth_metrics'::regclass
  ) then
    alter table public.earnings_quarterly_growth_metrics
      add constraint earnings_growth_revenue_state_values_ck check (
        (revenue_yoy_state = 'normal' or revenue_yoy_delta_pp is null)
        and (
          revenue_qoq_state = 'normal'
          or (
            revenue_qoq_seasonal_baseline_pct is null
            and revenue_qoq_seasonally_adjusted_pct is null
            and revenue_qoq_seasonally_adjusted_delta_pp is null
          )
        )
      );
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'earnings_growth_operating_state_values_ck'
      and conrelid = 'public.earnings_quarterly_growth_metrics'::regclass
  ) then
    alter table public.earnings_quarterly_growth_metrics
      add constraint earnings_growth_operating_state_values_ck check (
        (operating_income_yoy_state = 'normal' or operating_income_yoy_delta_pp is null)
        and (
          operating_income_qoq_state = 'normal'
          or (
            operating_income_qoq_seasonal_baseline_pct is null
            and operating_income_qoq_seasonally_adjusted_pct is null
            and operating_income_qoq_seasonally_adjusted_delta_pp is null
          )
        )
      );
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'earnings_growth_net_state_values_ck'
      and conrelid = 'public.earnings_quarterly_growth_metrics'::regclass
  ) then
    alter table public.earnings_quarterly_growth_metrics
      add constraint earnings_growth_net_state_values_ck check (
        (net_income_yoy_state = 'normal' or net_income_yoy_delta_pp is null)
        and (
          net_income_qoq_state = 'normal'
          or (
            net_income_qoq_seasonal_baseline_pct is null
            and net_income_qoq_seasonally_adjusted_pct is null
            and net_income_qoq_seasonally_adjusted_delta_pp is null
          )
        )
      );
  end if;
end
$$;
