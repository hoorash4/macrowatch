-- The Python domain calls the state completion_status; the V6 storage contract
-- calls it lifecycle_status. Accept either key at the RPC boundary so deployed
-- and rolling clients cannot write a null lifecycle.
create or replace function public.earnings_v2_v6_upsert_market_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.market_quarters as target (
    market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    average_operating_income, average_net_income, operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    actual_company_count, reported_company_count, pending_company_count, target_company_count,
    completion_status, lifecycle_status, calculation_version, calculated_at
  )
  select market_id, market_year, market_quarter, reference_date,
    top_line_total, operating_income_total, net_income_total,
    operating_income_total / nullif(target_company_count, 0),
    net_income_total / nullif(target_company_count, 0),
    operating_margin_pct, net_margin_pct,
    operating_income_yoy_pct, operating_income_yoy_state, net_income_yoy_pct, net_income_yoy_state,
    operating_income_qoq_sa_pct, operating_income_qoq_state, net_income_qoq_sa_pct, net_income_qoq_state,
    reported_company_count, reported_company_count, pending_company_count, target_company_count,
    case when coalesce(lifecycle_status, completion_status) = 'complete' then 'complete' else 'incomplete' end,
    coalesce(lifecycle_status, completion_status),
    coalesce(calculation_version, 6), coalesce(calculated_at, now())
  from jsonb_populate_recordset(null::earnings_v2.market_quarters, p_rows)
  on conflict (market_id, market_year, market_quarter) do update set
    reference_date = excluded.reference_date,
    top_line_total = excluded.top_line_total,
    operating_income_total = excluded.operating_income_total,
    net_income_total = excluded.net_income_total,
    average_operating_income = excluded.average_operating_income,
    average_net_income = excluded.average_net_income,
    operating_margin_pct = excluded.operating_margin_pct,
    net_margin_pct = excluded.net_margin_pct,
    operating_income_yoy_pct = excluded.operating_income_yoy_pct,
    operating_income_yoy_state = excluded.operating_income_yoy_state,
    net_income_yoy_pct = excluded.net_income_yoy_pct,
    net_income_yoy_state = excluded.net_income_yoy_state,
    operating_income_qoq_sa_pct = excluded.operating_income_qoq_sa_pct,
    operating_income_qoq_state = excluded.operating_income_qoq_state,
    net_income_qoq_sa_pct = excluded.net_income_qoq_sa_pct,
    net_income_qoq_state = excluded.net_income_qoq_state,
    actual_company_count = excluded.actual_company_count,
    reported_company_count = excluded.reported_company_count,
    pending_company_count = excluded.pending_company_count,
    target_company_count = excluded.target_company_count,
    completion_status = excluded.completion_status,
    lifecycle_status = excluded.lifecycle_status,
    calculation_version = excluded.calculation_version,
    calculated_at = excluded.calculated_at;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_v6_upsert_market_quarters(jsonb)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_upsert_market_quarters(jsonb)
  to service_role;
