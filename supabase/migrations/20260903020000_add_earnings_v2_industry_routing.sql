-- Industry is optional metadata used only to choose the missing-financial
-- fallback. It never participates in company or market completion.

alter table earnings_v2.companies
  add column if not exists industry_code text;

create or replace function public.earnings_v2_upsert_companies(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.companies as target
    (company_id, country, company_name, reporting_currency, entity_kind,
     industry_code, listed_from, delisted_on)
  select company_id, country, company_name, reporting_currency,
    coalesce(entity_kind, 'general'), industry_code, listed_from, delisted_on
  from jsonb_populate_recordset(null::earnings_v2.companies, p_rows)
  on conflict (company_id) do update set
    country = excluded.country,
    company_name = excluded.company_name,
    reporting_currency = excluded.reporting_currency,
    entity_kind = case
      when excluded.industry_code is not null then excluded.entity_kind
      else target.entity_kind
    end,
    industry_code = coalesce(excluded.industry_code, target.industry_code),
    listed_from = excluded.listed_from,
    delisted_on = excluded.delisted_on,
    updated_at = now();
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_upsert_company_profiles(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  with incoming as (
    select company_id, nullif(btrim(industry_code), '') as industry_code, entity_kind
    from jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb))
      as incoming_row(company_id text, industry_code text, entity_kind text)
    where entity_kind in ('general', 'financial')
  )
  update earnings_v2.companies as target
  set industry_code = incoming.industry_code,
      entity_kind = incoming.entity_kind,
      updated_at = now()
  from incoming
  where target.company_id = incoming.company_id
    and incoming.industry_code is not null;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

drop function if exists public.earnings_v2_v6_get_universe(text, integer, smallint);
create function public.earnings_v2_v6_get_universe(
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
  selection_method text,
  industry_code text,
  entity_kind text
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
    u.market_cap_rank, u.market_cap, u.currency, u.selection_method,
    c.industry_code, c.entity_kind
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

revoke all on function public.earnings_v2_upsert_company_profiles(jsonb)
  from public, anon, authenticated;
revoke all on function public.earnings_v2_v6_get_universe(text, integer, smallint)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_upsert_company_profiles(jsonb)
  to service_role;
grant execute on function public.earnings_v2_v6_get_universe(text, integer, smallint)
  to service_role;

comment on column earnings_v2.companies.industry_code is
  'Optional OpenDART KSIC industry code; routing metadata, not a completion field.';

