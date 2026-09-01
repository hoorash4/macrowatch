-- Complete the V2 read/checkpoint boundary without exposing private tables.

alter table earnings_v2.pipeline_state
  drop constraint if exists pipeline_state_status_check;
alter table earnings_v2.pipeline_state
  add constraint pipeline_state_status_check
  check (status in ('ready', 'running', 'incomplete', 'failed'));

create or replace function public.earnings_v2_get_company_quarters_many(p_company_ids text[])
returns setof earnings_v2.company_quarters
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.*
  from earnings_v2.company_quarters q
  where q.company_id = any(coalesce(p_company_ids, array[]::text[]))
  order by q.company_id, q.fiscal_year, q.fiscal_quarter;
$$;

create or replace function public.earnings_v2_get_market_quarters(p_market_id text)
returns setof earnings_v2.market_quarters
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select q.*
  from earnings_v2.market_quarters q
  where q.market_id = p_market_id
  order by q.market_year, q.market_quarter;
$$;

revoke all on function public.earnings_v2_get_company_quarters_many(text[]) from public, anon, authenticated;
revoke all on function public.earnings_v2_get_market_quarters(text) from public, anon, authenticated;
grant execute on function public.earnings_v2_get_company_quarters_many(text[]) to service_role;
grant execute on function public.earnings_v2_get_market_quarters(text) to service_role;

