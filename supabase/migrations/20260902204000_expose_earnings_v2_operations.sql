-- Read models for the public aggregate chart and the administrator's stale
-- pending queue. Company-level facts remain private to the service role.
create or replace function public.earnings_v2_public_market_series(p_market_id text)
returns table (
  market_year integer, market_quarter smallint, reference_date date,
  operating_income_total numeric, net_income_total numeric,
  operating_margin_pct numeric, net_margin_pct numeric,
  operating_income_yoy_pct numeric, operating_income_yoy_state text,
  net_income_yoy_pct numeric, net_income_yoy_state text,
  operating_income_qoq_sa_pct numeric, operating_income_qoq_state text,
  net_income_qoq_sa_pct numeric, net_income_qoq_state text,
  reported_company_count integer, pending_company_count integer,
  target_company_count integer, lifecycle_status text
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select
    q.market_year, q.market_quarter, q.reference_date,
    q.operating_income_total, q.net_income_total,
    q.operating_margin_pct, q.net_margin_pct,
    q.operating_income_yoy_pct, q.operating_income_yoy_state,
    q.net_income_yoy_pct, q.net_income_yoy_state,
    q.operating_income_qoq_sa_pct, q.operating_income_qoq_state,
    q.net_income_qoq_sa_pct, q.net_income_qoq_state,
    q.reported_company_count, q.pending_company_count,
    q.target_company_count, q.lifecycle_status
  from earnings_v2.market_quarters q
  where q.market_id = p_market_id
    and q.calculation_version >= 6
  order by q.market_year, q.market_quarter;
$$;

create or replace function public.earnings_v2_list_stale_pending()
returns table (
  market_id text, market_year integer, market_quarter smallint,
  company_id text, company_name text, stock_code text,
  missing_top_line boolean, missing_operating_income boolean,
  missing_net_income boolean, updated_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select distinct
    u.market_id, q.market_year, q.market_quarter,
    q.company_id, c.company_name,
    coalesce(krx.identifier_value, '') as stock_code,
    q.top_line is null, q.operating_income is null, q.net_income is null,
    q.updated_at
  from earnings_v2.company_quarters q
  join earnings_v2.universe_members u
    on u.company_id = q.company_id
   and u.market_year = q.market_year
   and u.market_quarter = q.market_quarter
  join earnings_v2.companies c on c.company_id = q.company_id
  left join lateral (
    select i.identifier_value
    from earnings_v2.company_identifiers i
    where i.company_id = q.company_id and i.identifier_type = 'krx_code'
    order by i.is_primary desc, i.valid_from desc
    limit 1
  ) krx on true
  where q.is_pending
    and q.calculation_version >= 6
    and exists (
      select 1
      from earnings_v2.universe_members later
      where later.market_id = u.market_id
        and later.market_year * 4 + later.market_quarter > q.market_year * 4 + q.market_quarter
    )
  order by q.market_year desc, q.market_quarter desc, c.company_name;
$$;

revoke all on function public.earnings_v2_public_market_series(text) from public;
grant execute on function public.earnings_v2_public_market_series(text) to anon, authenticated, service_role;
revoke all on function public.earnings_v2_list_stale_pending() from public, anon, authenticated;
grant execute on function public.earnings_v2_list_stale_pending() to service_role;
