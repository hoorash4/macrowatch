-- Retire the original public-schema earnings pipeline.
-- Earnings V2 is isolated in the earnings_v2 schema and has no dependency on
-- these objects. Historical V1 migrations remain as immutable history, while
-- this idempotent migration guarantees they cannot survive a full replay.

do $$
declare
  target record;
begin
  for target in
    select p.oid::regprocedure as signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and (
        (
          p.proname ilike '%earnings%'
          and p.proname not ilike 'earnings_v2_%'
        )
        or p.proname = 'upsert_sec_company_quarters'
      )
  loop
    execute format('drop function if exists %s cascade', target.signature);
  end loop;
end
$$;

drop table if exists public.earnings_company_price_gaps cascade;
drop table if exists public.earnings_company_quarterly_prices cascade;
drop table if exists public.earnings_quarterly_growth_metrics cascade;
drop table if exists public.earnings_market_quarterly_breadth cascade;
drop table if exists public.earnings_market_quarterly_metrics cascade;
drop table if exists public.earnings_quarterly_financials cascade;
drop table if exists public.earnings_ingestion_jobs cascade;
drop table if exists public.earnings_collection_checkpoints cascade;
drop table if exists public.earnings_universe_snapshots cascade;
drop table if exists public.earnings_filings cascade;
drop table if exists public.earnings_index_memberships cascade;
drop table if exists public.earnings_company_identifiers cascade;
drop table if exists public.earnings_indices cascade;
drop table if exists public.earnings_companies cascade;
