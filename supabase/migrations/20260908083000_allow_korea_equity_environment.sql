-- Serve versioned environment/momentum composites for both U.S. and Korean equities.
alter table public.liquidity_indices
  drop constraint if exists liquidity_indices_metric_check;

alter table public.liquidity_indices
  add constraint liquidity_indices_metric_check
  check (
    metric in ('pressure', 'capacity')
    or (country in ('US', 'KR') and metric in ('environment', 'momentum'))
  );
