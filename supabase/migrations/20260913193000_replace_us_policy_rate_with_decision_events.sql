-- U.S. policy rates are decision records, not daily observations.  The
-- subsequent explicit backfill repopulates this series from saved FOMC events
-- and, only for gaps, the corresponding official Fed statements.
alter table public.economic_chart_points
  drop constraint if exists economic_chart_points_frequency_check;

alter table public.economic_chart_points
  add constraint economic_chart_points_frequency_check
  check (frequency in ('D', 'W', 'T', 'M', 'E'));

delete from public.economic_chart_points
where series_code = 'US_POLICY_RATE_MID';
