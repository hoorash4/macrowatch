alter table public.economic_chart_points
  drop constraint if exists economic_chart_points_frequency_check;

alter table public.economic_chart_points
  add constraint economic_chart_points_frequency_check
  check (frequency in ('D', 'W', 'T', 'M', 'Q', 'E'));
