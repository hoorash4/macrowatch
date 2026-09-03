-- The public chart and supported Korean earnings history begin in 2019.
-- Keep older backfill rows intact, but exclude them from the administrator's
-- actionable pending queue.
create or replace function public.earnings_v2_list_pending()
returns table (
  market_id text, market_year integer, market_quarter smallint,
  company_id text, company_name text, stock_code text,
  top_line numeric, operating_income numeric, net_income numeric,
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
    q.top_line, q.operating_income, q.net_income,
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
    and q.market_year >= 2019
  order by q.market_year desc, q.market_quarter desc, c.company_name;
$$;

