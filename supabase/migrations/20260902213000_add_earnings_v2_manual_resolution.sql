-- Pending company facts are visible immediately and can be finalized by an
-- administrator. Manual rows are authoritative and collection reruns cannot
-- overwrite them unless a future explicit reset workflow is introduced.

alter table earnings_v2.company_quarters
  drop constraint if exists company_quarters_source_check;
alter table earnings_v2.company_quarters
  add constraint company_quarters_source_check
  check (source in ('open_dart', 'sec_edgar', 'manual'));

create or replace function earnings_v2.preserve_manual_company_quarter()
returns trigger
language plpgsql
set search_path = pg_catalog, earnings_v2
as $$
begin
  if old.source = 'manual' and new.source <> 'manual' then
    return old;
  end if;
  return new;
end;
$$;

drop trigger if exists earnings_v2_preserve_manual_company_quarter
  on earnings_v2.company_quarters;
create trigger earnings_v2_preserve_manual_company_quarter
before update on earnings_v2.company_quarters
for each row execute function earnings_v2.preserve_manual_company_quarter();

drop function if exists public.earnings_v2_list_stale_pending();
drop function if exists public.earnings_v2_list_pending();
create function public.earnings_v2_list_pending()
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
  where q.is_pending and q.calculation_version >= 6
  order by q.market_year desc, q.market_quarter desc, c.company_name;
$$;

create or replace function public.earnings_v2_resolve_pending(
  p_company_id text,
  p_fiscal_year integer,
  p_fiscal_quarter smallint,
  p_top_line numeric,
  p_operating_income numeric,
  p_net_income numeric
)
returns earnings_v2.company_quarters
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  resolved earnings_v2.company_quarters;
begin
  if p_top_line is null or p_operating_income is null or p_net_income is null then
    raise exception 'All three financial amounts are required';
  end if;

  update earnings_v2.company_quarters q
  set top_line = p_top_line,
      operating_income = p_operating_income,
      net_income = p_net_income,
      operating_margin_pct = case when p_top_line > 0 then p_operating_income / p_top_line * 100 else null end,
      net_margin_pct = case when p_top_line > 0 then p_net_income / p_top_line * 100 else null end,
      source = 'manual',
      source_filing_id = concat('manual:', p_company_id, ':', p_fiscal_year, 'Q', p_fiscal_quarter),
      revision_reference_date = current_date,
      quality_status = 'complete',
      is_pending = false,
      calculation_version = greatest(q.calculation_version, 6),
      updated_at = now()
  where q.company_id = p_company_id
    and q.fiscal_year = p_fiscal_year
    and q.fiscal_quarter = p_fiscal_quarter
    and q.is_pending
  returning q.* into resolved;

  if not found then
    raise exception 'Pending company quarter was not found';
  end if;
  return resolved;
end;
$$;

revoke all on function public.earnings_v2_list_pending() from public, anon, authenticated;
revoke all on function public.earnings_v2_resolve_pending(text, integer, smallint, numeric, numeric, numeric)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_list_pending() to service_role;
grant execute on function public.earnings_v2_resolve_pending(text, integer, smallint, numeric, numeric, numeric)
  to service_role;
