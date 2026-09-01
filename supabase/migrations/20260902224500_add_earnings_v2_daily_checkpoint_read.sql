-- The daily V2 collector advances this checkpoint only after a successful
-- discovery-and-update cycle. The boundary day's receipt IDs make the
-- inclusive date window idempotent without a fixed multi-day refetch.

create or replace function public.earnings_v2_get_pipeline_state(
  p_source text,
  p_operation text
)
returns setof earnings_v2.pipeline_state
language sql
stable
security definer
set search_path = pg_catalog, public, earnings_v2
as $$
  select state.*
  from earnings_v2.pipeline_state state
  where state.source = p_source and state.operation = p_operation
  limit 1;
$$;

revoke all on function public.earnings_v2_get_pipeline_state(text, text)
  from public, anon, authenticated;
grant execute on function public.earnings_v2_get_pipeline_state(text, text)
  to service_role;
