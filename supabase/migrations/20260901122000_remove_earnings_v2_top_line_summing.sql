-- Earnings V2 stores only explicit reported top-line totals.  The former
-- financial-income summation fallback was error-prone and has been removed
-- from the collector, so its now-constant provenance column is unnecessary.

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
    top_line, operating_income, net_income, currency, consolidation_scope,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, calculation_version
  )
  select
    company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, currency, consolidation_scope,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, coalesce(calculation_version, 1)
  from jsonb_populate_recordset(null::earnings_v2.company_quarters, p_rows)
  on conflict (company_id, fiscal_year, fiscal_quarter) do update set
    period_start = excluded.period_start,
    period_end = excluded.period_end,
    market_year = excluded.market_year,
    market_quarter = excluded.market_quarter,
    top_line = excluded.top_line,
    operating_income = excluded.operating_income,
    net_income = excluded.net_income,
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

alter table earnings_v2.company_quarters
  drop column top_line_method;

comment on table earnings_v2.company_quarters is
  'Single-quarter explicit reported facts plus UI-ready YoY and seasonally adjusted QoQ values; top-line values are never synthesized from income leaves.';
