-- Store quarter-to-quarter profit-margin changes as percentage-point values.
-- The pipeline calculates them from already persisted single-quarter margins;
-- no trigger or browser-side calculation is involved.

alter table earnings_v2.company_quarters
  add column if not exists operating_margin_qoq_delta_pctp numeric(20, 8),
  add column if not exists net_margin_qoq_delta_pctp numeric(20, 8);

alter table earnings_v2.market_quarters
  add column if not exists operating_margin_qoq_delta_pctp numeric(20, 8),
  add column if not exists net_margin_qoq_delta_pctp numeric(20, 8);

comment on column earnings_v2.company_quarters.operating_margin_qoq_delta_pctp is
  'Current single-quarter operating margin minus the immediately preceding quarter, in percentage points.';
comment on column earnings_v2.company_quarters.net_margin_qoq_delta_pctp is
  'Current single-quarter net margin minus the immediately preceding quarter, in percentage points.';
comment on column earnings_v2.market_quarters.operating_margin_qoq_delta_pctp is
  'Current aggregate operating margin minus the immediately preceding quarter, in percentage points.';
comment on column earnings_v2.market_quarters.net_margin_qoq_delta_pctp is
  'Current aggregate net margin minus the immediately preceding quarter, in percentage points.';

create or replace function public.earnings_v2_upsert_company_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.company_quarters as target (
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, operating_margin_pct, net_margin_pct,
    operating_margin_qoq_delta_pctp, net_margin_qoq_delta_pctp,
    currency, consolidation_scope,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, calculation_version
  )
  select
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, operating_margin_pct, net_margin_pct,
    operating_margin_qoq_delta_pctp, net_margin_qoq_delta_pctp,
    currency, consolidation_scope,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status,
    coalesce(calculation_version, 1)
  from jsonb_populate_recordset(null::earnings_v2.company_quarters, p_rows)
  on conflict (company_id, fiscal_year, fiscal_quarter) do update set
    period_start = excluded.period_start,
    period_end = excluded.period_end,
    market_year = excluded.market_year,
    market_quarter = excluded.market_quarter,
    top_line = excluded.top_line,
    operating_income = excluded.operating_income,
    net_income = excluded.net_income,
    operating_margin_pct = excluded.operating_margin_pct,
    net_margin_pct = excluded.net_margin_pct,
    operating_margin_qoq_delta_pctp = excluded.operating_margin_qoq_delta_pctp,
    net_margin_qoq_delta_pctp = excluded.net_margin_qoq_delta_pctp,
    currency = excluded.currency,
    consolidation_scope = excluded.consolidation_scope,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    source = excluded.source,
    source_filing_id = excluded.source_filing_id,
    filing_date = excluded.filing_date,
    revision_reference_date = excluded.revision_reference_date,
    quality_status = excluded.quality_status,
    calculation_version = excluded.calculation_version,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_upsert_market_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.market_quarters as target (
    market_id, market_year, market_quarter, average_operating_income, average_net_income,
    operating_margin_pct, net_margin_pct,
    operating_margin_qoq_delta_pctp, net_margin_qoq_delta_pctp,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, target_company_count, completion_status, calculation_version, calculated_at
  )
  select market_id, market_year, market_quarter, average_operating_income, average_net_income,
    operating_margin_pct, net_margin_pct,
    operating_margin_qoq_delta_pctp, net_margin_qoq_delta_pctp,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, target_company_count, completion_status, coalesce(calculation_version, 1),
    coalesce(calculated_at, now())
  from jsonb_populate_recordset(null::earnings_v2.market_quarters, p_rows)
  on conflict (market_id, market_year, market_quarter) do update set
    average_operating_income = excluded.average_operating_income,
    average_net_income = excluded.average_net_income,
    operating_margin_pct = excluded.operating_margin_pct,
    net_margin_pct = excluded.net_margin_pct,
    operating_margin_qoq_delta_pctp = excluded.operating_margin_qoq_delta_pctp,
    net_margin_qoq_delta_pctp = excluded.net_margin_qoq_delta_pctp,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    actual_company_count = excluded.actual_company_count,
    target_company_count = excluded.target_company_count,
    completion_status = excluded.completion_status,
    calculation_version = excluded.calculation_version,
    calculated_at = excluded.calculated_at;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_upsert_company_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_market_quarters(jsonb) from public, anon, authenticated;
grant execute on function public.earnings_v2_upsert_company_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_upsert_market_quarters(jsonb) to service_role;
