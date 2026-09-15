create or replace view public.economic_chart_series_coverage
with (security_invoker = true)
as
select
  series_code,
  min(observation_date) as first_date,
  max(observation_date) as last_date,
  count(*)::bigint as point_count
from public.economic_chart_series_points
group by series_code;

comment on view public.economic_chart_series_coverage is
  'Canonical coverage bounds used to determine Historical Insight series eligibility.';

revoke all on table public.economic_chart_series_coverage from public, anon, authenticated;
grant select on table public.economic_chart_series_coverage to authenticated, service_role;
