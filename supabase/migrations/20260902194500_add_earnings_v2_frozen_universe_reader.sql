-- V6 reruns must reuse the quarter-end universe rather than rediscovering and
-- replacing it. Return the frozen membership together with durable identifiers.
create or replace function public.earnings_v2_v6_get_universe(
  p_market_id text, p_market_year integer, p_market_quarter smallint
)
returns table (
  market_id text,
  market_year integer,
  market_quarter smallint,
  reference_date date,
  company_id text,
  company_name text,
  stock_code text,
  corp_code text,
  market_cap_rank integer,
  market_cap numeric,
  currency text,
  selection_method text
)
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select
    u.market_id, u.market_year, u.market_quarter, u.reference_date,
    u.company_id, c.company_name,
    krx.identifier_value as stock_code,
    dart.identifier_value as corp_code,
    u.market_cap_rank, u.market_cap, u.currency, u.selection_method
  from earnings_v2.universe_members u
  join earnings_v2.companies c on c.company_id = u.company_id
  join lateral (
    select i.identifier_value
    from earnings_v2.company_identifiers i
    where i.company_id = u.company_id
      and i.identifier_type = 'krx_code'
      and i.valid_from <= u.reference_date
      and (i.valid_to is null or i.valid_to >= u.reference_date)
    order by i.is_primary desc, i.valid_from desc
    limit 1
  ) krx on true
  join lateral (
    select i.identifier_value
    from earnings_v2.company_identifiers i
    where i.company_id = u.company_id
      and i.identifier_type = 'dart_corp_code'
      and i.valid_from <= u.reference_date
      and (i.valid_to is null or i.valid_to >= u.reference_date)
    order by i.is_primary desc, i.valid_from desc
    limit 1
  ) dart on true
  where u.market_id = p_market_id
    and u.market_year = p_market_year
    and u.market_quarter = p_market_quarter
  order by u.market_cap_rank;
$$;

revoke all on function public.earnings_v2_v6_get_universe(text, integer, smallint)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_get_universe(text, integer, smallint)
  to service_role;
