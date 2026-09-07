-- Versioned US environment/momentum results alongside the existing KR metrics.
alter table public.liquidity_indices drop constraint liquidity_indices_metric_check;
alter table public.liquidity_indices add constraint liquidity_indices_metric_check check (metric in ('pressure', 'capacity') or (country = 'US' and metric in ('environment', 'momentum')));

