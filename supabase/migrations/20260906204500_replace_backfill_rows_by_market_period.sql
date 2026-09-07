-- Backfill replacement is authoritative for the physical market period.
-- SEC comparative facts can reuse fiscal labels across filings, so deleting
-- only by the fiscal key can leave an older row for the same calendar quarter.
create or replace function public.earnings_v2_v6_replace_company_quarters_for_backfill(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare v_count integer;
begin
  if jsonb_typeof(coalesce(p_rows, '[]'::jsonb)) <> 'array' then
    raise exception 'Backfill rows must be a JSON array';
  end if;

  delete from earnings_v2.company_quarters q
  using jsonb_to_recordset(coalesce(p_rows, '[]'::jsonb))
    as incoming(company_id text, market_year integer, market_quarter smallint)
  where q.company_id = incoming.company_id
    and q.market_year = incoming.market_year
    and q.market_quarter = incoming.market_quarter;

  v_count := public.earnings_v2_v6_upsert_company_quarters(p_rows);
  return v_count;
end;
$$;

revoke all on function public.earnings_v2_v6_replace_company_quarters_for_backfill(jsonb)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_v6_replace_company_quarters_for_backfill(jsonb)
  to service_role;

