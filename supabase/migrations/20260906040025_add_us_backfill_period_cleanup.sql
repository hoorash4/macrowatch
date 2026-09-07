-- Remove only one failed U.S. earnings backfill period. Previously completed
-- periods and Korean earnings are outside this function's scope.
create or replace function public.earnings_v2_us_clear_backfill_period(
  p_market_year integer,
  p_market_quarter smallint
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
declare
  v_company_rows integer := 0;
  v_market_rows integer := 0;
begin
  if p_market_quarter not between 1 and 4 then
    raise exception 'Unsupported market quarter: %', p_market_quarter;
  end if;

  delete from earnings_v2.company_quarters q
   where q.market_year = p_market_year
     and q.market_quarter = p_market_quarter
     and exists (
       select 1
         from earnings_v2.universe_members u
        where u.company_id = q.company_id
          and u.market_year = p_market_year
          and u.market_quarter = p_market_quarter
          and u.market_id in ('us_sp100', 'us_nasdaq100')
     );
  get diagnostics v_company_rows = row_count;

  delete from earnings_v2.market_quarters m
   where m.market_year = p_market_year
     and m.market_quarter = p_market_quarter
     and m.market_id in ('us_sp100', 'us_nasdaq100');
  get diagnostics v_market_rows = row_count;

  return jsonb_build_object(
    'company_rows_deleted', v_company_rows,
    'market_rows_deleted', v_market_rows
  );
end;
$$;

revoke all on function public.earnings_v2_us_clear_backfill_period(integer, smallint)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_us_clear_backfill_period(integer, smallint)
  to service_role;
