-- Incremental seasonal-adjustment state.
-- Raw company/market quarter history remains canonical; this compact window only
-- avoids rereading and recalculating the full history for every new filing.

create table if not exists earnings_v2.seasonal_windows (
  entity_type text not null check (entity_type in ('company', 'market')),
  entity_id text not null,
  metric text not null check (metric in ('operating_income', 'net_income')),
  fiscal_quarter smallint not null check (fiscal_quarter between 1 and 4),
  sample_years smallint[] not null default '{}',
  sample_values numeric[] not null default '{}',
  updated_at timestamptz not null default now(),
  primary key (entity_type, entity_id, metric, fiscal_quarter),
  check (cardinality(sample_years) = cardinality(sample_values)),
  check (cardinality(sample_years) <= 10)
);

alter table earnings_v2.seasonal_windows enable row level security;
revoke all on earnings_v2.seasonal_windows from public, anon, authenticated;
grant select, insert, update, delete on earnings_v2.seasonal_windows to service_role;

create or replace function public.earnings_v2_get_seasonal_windows(
  p_entity_type text,
  p_entity_ids text[]
)
returns setof earnings_v2.seasonal_windows
language sql
stable
security invoker
set search_path = pg_catalog, public, earnings_v2
as $$
  select w.*
  from earnings_v2.seasonal_windows w
  where w.entity_type = p_entity_type
    and w.entity_id = any(p_entity_ids);
$$;

create or replace function public.earnings_v2_upsert_seasonal_windows(p_rows jsonb)
returns integer
language plpgsql
security invoker
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  insert into earnings_v2.seasonal_windows as target (
    entity_type, entity_id, metric, fiscal_quarter,
    sample_years, sample_values, updated_at
  )
  select entity_type, entity_id, metric, fiscal_quarter,
    sample_years, sample_values, now()
  from jsonb_populate_recordset(null::earnings_v2.seasonal_windows, p_rows)
  on conflict (entity_type, entity_id, metric, fiscal_quarter) do update set
    sample_years = excluded.sample_years,
    sample_values = excluded.sample_values,
    updated_at = excluded.updated_at;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

create or replace function public.earnings_v2_get_company_quarters_for_periods(
  p_company_ids text[],
  p_periods jsonb
)
returns setof earnings_v2.company_quarters
language sql
stable
security invoker
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.*
  from earnings_v2.company_quarters q
  join jsonb_to_recordset(p_periods) as period(fiscal_year integer, fiscal_quarter smallint)
    on period.fiscal_year = q.fiscal_year
   and period.fiscal_quarter = q.fiscal_quarter
  where q.company_id = any(p_company_ids)
  order by q.company_id, q.fiscal_year, q.fiscal_quarter;
$$;

create or replace function public.earnings_v2_get_market_quarters_for_periods(
  p_market_ids text[],
  p_periods jsonb
)
returns setof earnings_v2.market_quarters
language sql
stable
security invoker
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.*
  from earnings_v2.market_quarters q
  join jsonb_to_recordset(p_periods) as period(market_year integer, market_quarter smallint)
    on period.market_year = q.market_year
   and period.market_quarter = q.market_quarter
  where q.market_id = any(p_market_ids)
  order by q.market_id, q.market_year, q.market_quarter;
$$;

revoke all on function public.earnings_v2_get_seasonal_windows(text, text[]) from public, anon, authenticated;
revoke all on function public.earnings_v2_upsert_seasonal_windows(jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_company_quarters_for_periods(text[], jsonb) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_market_quarters_for_periods(text[], jsonb) from public, anon, authenticated;
grant execute on function public.earnings_v2_get_seasonal_windows(text, text[]) to service_role;
grant execute on function public.earnings_v2_upsert_seasonal_windows(jsonb) to service_role;
grant execute on function public.earnings_v2_get_company_quarters_for_periods(text[], jsonb) to service_role;
grant execute on function public.earnings_v2_get_market_quarters_for_periods(text[], jsonb) to service_role;

comment on table earnings_v2.seasonal_windows is
  'Rolling cache of at most ten same-season raw QoQ transitions. It is derived state, never a replacement for canonical quarter facts.';
