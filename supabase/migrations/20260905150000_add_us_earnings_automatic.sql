-- U.S. earnings uses the V2 fact tables, but has a separate collection path.
-- A fiscal period may end in a different calendar quarter, so every reader
-- below keeps the SEC fiscal key and the displayed market key distinct.

alter table earnings_v2.universe_members
  drop constraint if exists universe_members_market_id_check;
alter table earnings_v2.universe_members
  add constraint universe_members_market_id_check
  check (market_id in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq', 'us_nyse'));

alter table earnings_v2.market_quarters
  drop constraint if exists market_quarters_market_id_check;
alter table earnings_v2.market_quarters
  add constraint market_quarters_market_id_check
  check (market_id in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq', 'us_nyse'));

create or replace function public.earnings_v2_replace_universe(
  p_market_id text, p_market_year integer, p_market_quarter smallint, p_rows jsonb
)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  if p_market_id not in ('kr_largecap', 'kr_kosdaq', 'us_largecap', 'us_nasdaq', 'us_nyse') then
    raise exception 'Unsupported earnings V2 market: %', p_market_id;
  end if;
  delete from earnings_v2.universe_members
   where market_id = p_market_id and market_year = p_market_year and market_quarter = p_market_quarter;
  insert into earnings_v2.universe_members (
    market_id, market_year, market_quarter, reference_date, company_id,
    market_cap_rank, market_cap, currency, selection_method
  )
  select market_id, market_year, market_quarter, reference_date, company_id,
         market_cap_rank, market_cap, currency, selection_method
    from jsonb_populate_recordset(null::earnings_v2.universe_members, p_rows);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_us_get_universe(
  p_market_id text, p_market_year integer, p_market_quarter smallint
)
returns table (
  market_id text, market_year integer, market_quarter smallint, reference_date date,
  company_id text, company_name text, ticker text, cik text,
  market_cap_rank integer, market_cap numeric, currency text
)
language sql stable security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select u.market_id, u.market_year, u.market_quarter, u.reference_date,
         u.company_id, c.company_name, ticker.identifier_value, cik.identifier_value,
         u.market_cap_rank, u.market_cap, u.currency
    from earnings_v2.universe_members u
    join earnings_v2.companies c on c.company_id = u.company_id
    left join lateral (
      select i.identifier_value from earnings_v2.company_identifiers i
       where i.company_id = u.company_id and i.identifier_type = 'ticker'
         and i.valid_from <= u.reference_date
         and (i.valid_to is null or i.valid_to >= u.reference_date)
       order by i.is_primary desc, i.valid_from desc limit 1
    ) ticker on true
    left join lateral (
      select i.identifier_value from earnings_v2.company_identifiers i
       where i.company_id = u.company_id and i.identifier_type = 'cik'
       order by i.is_primary desc, i.valid_from desc limit 1
    ) cik on true
   where u.market_id = p_market_id
     and u.market_year = p_market_year and u.market_quarter = p_market_quarter
   order by u.market_cap_rank;
$$;

create or replace function public.earnings_v2_us_active_companies(p_since_year integer)
returns table (company_id text, company_name text, ticker text, cik text)
language sql stable security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select distinct on (u.company_id) u.company_id, c.company_name,
         ticker.identifier_value, cik.identifier_value
    from earnings_v2.universe_members u
    join earnings_v2.companies c on c.company_id = u.company_id
    left join lateral (
      select i.identifier_value from earnings_v2.company_identifiers i
       where i.company_id = u.company_id and i.identifier_type = 'ticker'
       order by i.is_primary desc, i.valid_from desc limit 1
    ) ticker on true
    left join lateral (
      select i.identifier_value from earnings_v2.company_identifiers i
       where i.company_id = u.company_id and i.identifier_type = 'cik'
       order by i.is_primary desc, i.valid_from desc limit 1
    ) cik on true
   where u.market_id in ('us_nyse', 'us_nasdaq') and u.market_year >= p_since_year
   order by u.company_id, u.market_year desc, u.market_quarter desc;
$$;

create or replace function public.earnings_v2_us_market_facts(
  p_market_id text, p_market_year integer, p_market_quarter smallint
)
returns setof earnings_v2.company_quarters
language sql stable security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.* from earnings_v2.company_quarters q
  join earnings_v2.universe_members u
    on u.company_id = q.company_id
   and u.market_year = q.market_year and u.market_quarter = q.market_quarter
  where u.market_id = p_market_id
    and u.market_year = p_market_year and u.market_quarter = p_market_quarter
  order by u.market_cap_rank;
$$;

revoke all on function public.earnings_v2_us_get_universe(text, integer, smallint) from public;
revoke all on function public.earnings_v2_us_active_companies(integer) from public;
revoke all on function public.earnings_v2_us_market_facts(text, integer, smallint) from public;
grant execute on function public.earnings_v2_us_get_universe(text, integer, smallint) to service_role;
grant execute on function public.earnings_v2_us_active_companies(integer) to service_role;
grant execute on function public.earnings_v2_us_market_facts(text, integer, smallint) to service_role;
