-- Browser clients need only the current 200-company chooser and the selected
-- company's chart fields; operational company facts remain private.
create function public.earnings_v2_public_latest_company_options()
returns table (
  company_id text,
  company_name text,
  market_id text,
  market_cap_rank integer,
  market_cap numeric
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  with latest_periods as (
    select market_id, max(market_year * 4 + market_quarter) as period_key
    from earnings_v2.universe_members
    where market_id in ('kr_largecap', 'kr_kosdaq')
    group by market_id
  )
  select
    u.company_id,
    c.company_name,
    u.market_id,
    u.market_cap_rank,
    u.market_cap
  from earnings_v2.universe_members u
  join latest_periods latest
    on latest.market_id = u.market_id
   and latest.period_key = u.market_year * 4 + u.market_quarter
  join earnings_v2.companies c on c.company_id = u.company_id
  order by u.market_cap desc, u.market_id, u.market_cap_rank;
$$;

create function public.earnings_v2_public_company_series(p_company_id text)
returns table (
  fiscal_year integer,
  fiscal_quarter smallint,
  operating_income numeric,
  net_income numeric,
  operating_margin_pct numeric,
  net_margin_pct numeric,
  operating_income_yoy_pct numeric,
  operating_income_yoy_state text,
  net_income_yoy_pct numeric,
  net_income_yoy_state text,
  operating_income_qoq_sa_pct numeric,
  operating_income_qoq_state text,
  net_income_qoq_sa_pct numeric,
  net_income_qoq_state text,
  is_pending boolean
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  with latest_periods as (
    select market_id, max(market_year * 4 + market_quarter) as period_key
    from earnings_v2.universe_members
    where market_id in ('kr_largecap', 'kr_kosdaq')
    group by market_id
  ), current_candidates as (
    select u.company_id
    from earnings_v2.universe_members u
    join latest_periods latest
      on latest.market_id = u.market_id
     and latest.period_key = u.market_year * 4 + u.market_quarter
    where u.company_id = p_company_id
  )
  select
    q.fiscal_year,
    q.fiscal_quarter,
    q.operating_income,
    q.net_income,
    q.operating_margin_pct,
    q.net_margin_pct,
    q.operating_income_yoy_pct,
    q.operating_income_yoy_state,
    q.net_income_yoy_pct,
    q.net_income_yoy_state,
    q.operating_income_qoq_sa_pct,
    q.operating_income_qoq_state,
    q.net_income_qoq_sa_pct,
    q.net_income_qoq_state,
    q.is_pending
  from earnings_v2.company_quarters q
  join current_candidates candidate on candidate.company_id = q.company_id
  where q.calculation_version >= 6
  order by q.fiscal_year, q.fiscal_quarter;
$$;

revoke all on function public.earnings_v2_public_latest_company_options() from public;
revoke all on function public.earnings_v2_public_company_series(text) from public;
grant execute on function public.earnings_v2_public_latest_company_options() to anon, authenticated, service_role;
grant execute on function public.earnings_v2_public_company_series(text) to anon, authenticated, service_role;
