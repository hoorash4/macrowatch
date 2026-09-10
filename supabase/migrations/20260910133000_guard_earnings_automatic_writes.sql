-- Separate automatic earnings writes from historical repair/backfill writes.
-- Automatic RPCs accept only the latest completed market quarter in Asia/Seoul.
-- Existing unrestricted RPCs remain available only to explicitly manual/repair/backfill code paths.

create or replace function earnings_v2.latest_completed_market_period()
returns table(market_year integer, market_quarter smallint)
language sql
stable
set search_path = pg_catalog
as $$
  with local_day as (
    select (now() at time zone 'Asia/Seoul')::date as d
  ), current_period as (
    select
      extract(year from d)::integer as y,
      (((extract(month from d)::integer - 1) / 3) + 1)::integer as q
    from local_day
  )
  select
    case when q = 1 then y - 1 else y end,
    (case when q = 1 then 4 else q - 1 end)::smallint
  from current_period;
$$;

create or replace function earnings_v2.assert_latest_completed_period(
  p_year integer,
  p_quarter smallint,
  p_operation text
)
returns void
language plpgsql
stable
set search_path = pg_catalog, earnings_v2
as $$
declare
  v_year integer;
  v_quarter smallint;
begin
  select market_year, market_quarter
    into v_year, v_quarter
  from earnings_v2.latest_completed_market_period();

  if p_year is distinct from v_year or p_quarter is distinct from v_quarter then
    raise exception 'automatic earnings write blocked for %Q% during %; only %Q% is writable',
      p_year, p_quarter, p_operation, v_year, v_quarter
      using errcode = '22023';
  end if;
end;
$$;

create or replace function public.earnings_v2_auto_replace_universe(
  p_market_id text,
  p_market_year integer,
  p_market_quarter smallint,
  p_rows jsonb
)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
begin
  perform earnings_v2.assert_latest_completed_period(
    p_market_year, p_market_quarter, 'replace_universe'
  );
  return public.earnings_v2_replace_universe(
    p_market_id, p_market_year, p_market_quarter, p_rows
  );
end;
$$;

create or replace function public.earnings_v2_auto_v6_upsert_company_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  v_row jsonb;
  v_year integer;
  v_quarter smallint;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'p_rows must be a JSON array' using errcode = '22023';
  end if;

  for v_row in select value from jsonb_array_elements(p_rows)
  loop
    v_year := coalesce(
      nullif(v_row ->> 'market_year', '')::integer,
      nullif(v_row ->> 'fiscal_year', '')::integer
    );
    v_quarter := coalesce(
      nullif(v_row ->> 'market_quarter', '')::smallint,
      nullif(v_row ->> 'fiscal_quarter', '')::smallint
    );
    if v_year is null or v_quarter is null then
      raise exception 'automatic company-quarter write requires a market/fiscal period'
        using errcode = '22023';
    end if;
    perform earnings_v2.assert_latest_completed_period(
      v_year, v_quarter, 'upsert_company_quarters'
    );
  end loop;

  return public.earnings_v2_v6_upsert_company_quarters(p_rows);
end;
$$;

create or replace function public.earnings_v2_auto_v6_upsert_market_quarters(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  v_row jsonb;
  v_year integer;
  v_quarter smallint;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'p_rows must be a JSON array' using errcode = '22023';
  end if;

  for v_row in select value from jsonb_array_elements(p_rows)
  loop
    v_year := nullif(v_row ->> 'market_year', '')::integer;
    v_quarter := nullif(v_row ->> 'market_quarter', '')::smallint;
    if v_year is null or v_quarter is null then
      raise exception 'automatic market-quarter write requires a market period'
        using errcode = '22023';
    end if;
    perform earnings_v2.assert_latest_completed_period(
      v_year, v_quarter, 'upsert_market_quarters'
    );
  end loop;

  return public.earnings_v2_v6_upsert_market_quarters(p_rows);
end;
$$;

create or replace function public.earnings_v2_auto_upsert_quarter_fx_rate(p_row jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  v_year integer := nullif(p_row ->> 'fiscal_year', '')::integer;
  v_quarter smallint := nullif(p_row ->> 'fiscal_quarter', '')::smallint;
begin
  if v_year is null or v_quarter is null then
    raise exception 'automatic FX write requires a fiscal period' using errcode = '22023';
  end if;
  perform earnings_v2.assert_latest_completed_period(
    v_year, v_quarter, 'upsert_quarter_fx_rate'
  );
  return public.earnings_v2_upsert_quarter_fx_rate(p_row);
end;
$$;

revoke all on function public.earnings_v2_auto_replace_universe(text, integer, smallint, jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_auto_v6_upsert_company_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_auto_v6_upsert_market_quarters(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_auto_upsert_quarter_fx_rate(jsonb) from public, anon, authenticated;
grant execute on function public.earnings_v2_auto_replace_universe(text, integer, smallint, jsonb) to service_role;
grant execute on function public.earnings_v2_auto_v6_upsert_company_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_auto_v6_upsert_market_quarters(jsonb) to service_role;
grant execute on function public.earnings_v2_auto_upsert_quarter_fx_rate(jsonb) to service_role;

comment on function public.earnings_v2_auto_replace_universe(text, integer, smallint, jsonb)
  is 'Automatic-only universe writer; rejects every quarter except the latest completed market quarter.';
comment on function public.earnings_v2_auto_v6_upsert_company_quarters(jsonb)
  is 'Automatic-only company-quarter writer; rejects historical and future quarter mutations.';
comment on function public.earnings_v2_auto_v6_upsert_market_quarters(jsonb)
  is 'Automatic-only market-quarter writer; rejects historical and future quarter mutations.';
comment on function public.earnings_v2_auto_upsert_quarter_fx_rate(jsonb)
  is 'Automatic-only quarter FX writer; rejects historical and future quarter mutations.';
