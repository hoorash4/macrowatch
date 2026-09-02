-- Backfill is a source-authoritative replacement. Keep the three cumulative
-- source amounts required to derive the next standalone quarter without a
-- second bulk OpenDART request. These are source facts, not UI duplicates.

alter table earnings_v2.company_quarters
  add column if not exists source_currency text,
  add column if not exists source_top_line_cumulative numeric(38, 4),
  add column if not exists source_operating_income_cumulative numeric(38, 4),
  add column if not exists source_net_income_cumulative numeric(38, 4);

update earnings_v2.company_quarters
set source_currency = currency
where source_currency is null;

alter table earnings_v2.company_quarters
  alter column source_currency set default 'KRW',
  alter column source_currency set not null;

create or replace function public.earnings_v2_v6_upsert_company_quarters(p_rows jsonb)
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
    currency, source_currency, consolidation_scope,
    source_top_line_cumulative, source_operating_income_cumulative, source_net_income_cumulative,
    is_pending,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date, quality_status, calculation_version
  )
  select company_id, fiscal_year, fiscal_quarter, period_start, period_end, market_year, market_quarter,
    top_line, operating_income, net_income, operating_margin_pct, net_margin_pct,
    currency, coalesce(source_currency, currency), consolidation_scope,
    source_top_line_cumulative, source_operating_income_cumulative, source_net_income_cumulative,
    coalesce(is_pending, true),
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    source, source_filing_id, filing_date, revision_reference_date,
    case when coalesce(is_pending, true) then 'review_required' else 'complete' end,
    coalesce(calculation_version, 6)
  from jsonb_populate_recordset(null::earnings_v2.company_quarters, p_rows)
  on conflict (company_id, fiscal_year, fiscal_quarter) do update set
    period_start = excluded.period_start, period_end = excluded.period_end,
    market_year = excluded.market_year, market_quarter = excluded.market_quarter,
    top_line = excluded.top_line, operating_income = excluded.operating_income, net_income = excluded.net_income,
    operating_margin_pct = excluded.operating_margin_pct, net_margin_pct = excluded.net_margin_pct,
    currency = excluded.currency, source_currency = excluded.source_currency,
    consolidation_scope = excluded.consolidation_scope,
    source_top_line_cumulative = excluded.source_top_line_cumulative,
    source_operating_income_cumulative = excluded.source_operating_income_cumulative,
    source_net_income_cumulative = excluded.source_net_income_cumulative,
    is_pending = excluded.is_pending, quality_status = excluded.quality_status,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct, net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    source = excluded.source, source_filing_id = excluded.source_filing_id,
    filing_date = excluded.filing_date, revision_reference_date = excluded.revision_reference_date,
    calculation_version = excluded.calculation_version, updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- Keep the global manual-row protection in place. Only this explicit backfill
-- RPC may replace an exact submitted company-period key. Delete and insert are
-- one transaction, so a failed insert rolls the deletion back atomically.
create or replace function public.earnings_v2_v6_replace_company_quarters_for_backfill(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  if jsonb_typeof(coalesce(p_rows, '[]'::jsonb)) <> 'array' then
    raise exception 'Backfill rows must be a JSON array';
  end if;

  delete from earnings_v2.company_quarters q
  using jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb))
    as incoming(company_id text, fiscal_year integer, fiscal_quarter smallint)
  where q.company_id = incoming.company_id
    and q.fiscal_year = incoming.fiscal_year
    and q.fiscal_quarter = incoming.fiscal_quarter;

  v_count := public.earnings_v2_v6_upsert_company_quarters(p_rows);
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_v6_replace_company_quarters_for_backfill(jsonb)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_replace_company_quarters_for_backfill(jsonb)
  to service_role;

comment on column earnings_v2.company_quarters.source_currency is
  'Currency of cumulative OpenDART source amounts before any quarter-end FX conversion.';
comment on column earnings_v2.company_quarters.source_top_line_cumulative is
  'OpenDART cumulative top line retained only for next-quarter standalone derivation.';
comment on column earnings_v2.company_quarters.source_operating_income_cumulative is
  'OpenDART cumulative operating income retained only for next-quarter standalone derivation.';
comment on column earnings_v2.company_quarters.source_net_income_cumulative is
  'OpenDART cumulative net income retained only for next-quarter standalone derivation.';
