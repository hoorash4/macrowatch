-- Expose only the last complete point-in-time market-cap snapshot observed in
-- each calendar quarter.  The calculator must not infer missing history from
-- today's constituents.
create or replace function public.list_quarterly_earnings_universes()
returns table (
  index_id text,
  observed_on date,
  company_id uuid,
  rank smallint
)
language sql
stable
security definer
set search_path = public
as $$
  with latest as (
    select snapshots.index_id,
           date_trunc('quarter', snapshots.observed_on)::date as quarter_start,
           max(snapshots.observed_on) as observed_on
    from public.earnings_universe_snapshots snapshots
    group by snapshots.index_id, date_trunc('quarter', snapshots.observed_on)::date
  )
  select snapshots.index_id, snapshots.observed_on,
         snapshots.company_id, snapshots.rank
  from latest
  join public.earnings_universe_snapshots snapshots
    on snapshots.index_id = latest.index_id
   and snapshots.observed_on = latest.observed_on
  order by snapshots.index_id, snapshots.observed_on, snapshots.rank
$$;

revoke all on function public.list_quarterly_earnings_universes()
  from public, anon, authenticated;
grant execute on function public.list_quarterly_earnings_universes()
  to service_role;

comment on function public.list_quarterly_earnings_universes() is
  'Latest complete point-in-time market-cap universe observed inside each calendar quarter.';

-- Distinguish an absent historical universe from a real zero earnings base.
alter table public.earnings_market_quarterly_metrics
  drop constraint if exists earnings_market_quarterly_metrics_yoy_state_check;
alter table public.earnings_market_quarterly_metrics
  add constraint earnings_market_quarterly_metrics_yoy_state_check
  check (yoy_state in (
    'normal', 'black_turn', 'red_turn', 'loss_narrowing',
    'loss_widening', 'loss_unchanged', 'from_zero',
    'missing_prior_snapshot', 'insufficient_coverage'
  ));

comment on table public.earnings_market_quarterly_metrics is
  'Point-in-time market-cap constituent aggregates; each quarter uses that quarter''s actual ranked universe.';

-- Remove only recalculable derivatives produced by the superseded
-- current-membership reconstruction. Canonical company financials, prices and
-- point-in-time universe snapshots are deliberately untouched.
delete from public.earnings_market_quarterly_metrics
where universe_basis <> 'point_in_time_market_cap_snapshot';

delete from public.earnings_market_quarterly_breadth
where universe_basis <> 'point_in_time_market_cap_snapshot';
